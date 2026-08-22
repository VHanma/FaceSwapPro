package org.vaan.faceswap.v2.engine

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.ImageDecoder
import android.graphics.Paint
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.ConcurrentHashMap

/**
 * v2 neural replacement path:
 * pose-aware source selection -> ArcFace -> CrossFace -> SimSwap512 ->
 * semantic + arbitrary occlusion gating -> spatial relighting -> micro-detail
 * restoration -> independent quality gate/alternate-reference rerender ->
 * expression-aware temporal stabilization -> composite.
 */
class MovieFaceSwapEngine(
    private val context: Context,
    private val vault: DefaultIdentityVault,
    private val pack: NeuralModelPackManager,
) : AutoCloseable {

    data class SwapResult(
        val bitmap: Bitmap,
        val sourceReference: IdentityReference,
        val targetPose: FacePoseQuality.Pose,
        val quality: FrameQuality,
        val rerendered: Boolean,
    )

    private data class Candidate(
        val reference: IdentityReference,
        val restored: Bitmap,
        val evaluation: FrameQualityEvaluator.Candidate,
    )

    private val arcFace = ArcFaceR50Encoder(pack.file("arcface_w600k_r50.onnx"))
    private val converter = CrossFaceSimSwapConverter(pack.file("crossface_simswap.onnx"))
    private val swapper = SimSwap512Engine(pack.file("simswap_unofficial_512.onnx"))
    private val parser = SemanticFaceParser(context)
    private val occluder = FaceOcclusionMasker(pack.file("xseg_3.onnx"))
    private val sourceAnalyzer = SourceFaceAnalyzer(context)
    private val temporal = AlignedTemporalStabilizer()
    private val qualityEvaluator = FrameQualityEvaluator(context, vault)
    private val convertedEmbeddingCache = ConcurrentHashMap<String, FloatArray>()

    suspend fun swapFrame(
        targetFrame: Bitmap,
        targetFace: TrackerManager.TrackedFace,
        allowRerender: Boolean = true,
        minimumQuality: Float = 0.78f,
    ): SwapResult = withContext(Dispatchers.Default) {
        val pose = FacePoseQuality.estimatePose(targetFace)
        val rankedReferences = vault.rankedReferences(pose.yaw, pose.pitch)
        val primaryReference = rankedReferences.firstOrNull()
            ?: error("Identity Vault has no usable source reference")

        val targetPoints = FaceAligner.fivePoints(
            targetFace.landmarks,
            targetFrame.width,
            targetFrame.height,
        )
        val alignment = FaceAligner.align(
            bitmap = targetFrame,
            points = targetPoints,
            template = FaceAligner.Template.ARC_FACE_112_V1,
            width = SimSwap512Engine.SIZE,
        )

        try {
            // Target analysis is shared across rerender candidates.
            val semantic = parser.parse(alignment.bitmap)
            val semanticMask = semantic.identityCompositeMask()
            val visibleMask = occluder.visibleFaceMask(alignment.bitmap, semantic.width)
            val rawMask = intersectMasks(semanticMask, visibleMask)
            val visibleSkin = intersectMasks(
                semantic.binaryMask(SemanticFaceParser.Region.SKIN),
                visibleMask,
            )
            val alphaMask = MaskFeather.feather(
                mask = rawMask,
                width = semantic.width,
                height = semantic.height,
                radius = 7,
                passes = 2,
            )

            var chosen = renderCandidate(
                reference = primaryReference,
                targetFrame = targetFrame,
                targetFace = targetFace,
                pose = pose,
                alignment = alignment,
                rawMask = rawMask,
                visibleSkin = visibleSkin,
                alphaMask = alphaMask,
            )
            var rerendered = false

            if (
                allowRerender &&
                chosen.evaluation.quality.overall < minimumQuality &&
                rankedReferences.size > 1
            ) {
                rerendered = true
                val alternate = renderCandidate(
                    reference = rankedReferences[1],
                    targetFrame = targetFrame,
                    targetFace = targetFace,
                    pose = pose,
                    alignment = alignment,
                    rawMask = rawMask,
                    visibleSkin = visibleSkin,
                    alphaMask = alphaMask,
                )
                if (alternate.evaluation.quality.overall > chosen.evaluation.quality.overall) {
                    chosen.restored.recycle()
                    chosen = alternate
                } else {
                    alternate.restored.recycle()
                }
            }

            qualityEvaluator.accept(chosen.evaluation)
            val stabilized = temporal.stabilize(
                current = chosen.restored,
                alpha = alphaMask,
                face = targetFace,
                pose = pose,
            )
            val maskedGenerated = applyAlpha(stabilized.bitmap, stabilized.alpha)
            val output = targetFrame.copy(Bitmap.Config.ARGB_8888, true)
            Canvas(output).drawBitmap(
                maskedGenerated,
                alignment.alignedToSource,
                Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG),
            )

            maskedGenerated.recycle()
            stabilized.bitmap.recycle()
            chosen.restored.recycle()

            SwapResult(
                bitmap = output,
                sourceReference = chosen.reference,
                targetPose = pose,
                quality = chosen.evaluation.quality,
                rerendered = rerendered,
            )
        } finally {
            alignment.bitmap.recycle()
        }
    }

    private fun renderCandidate(
        reference: IdentityReference,
        targetFrame: Bitmap,
        targetFace: TrackerManager.TrackedFace,
        pose: FacePoseQuality.Pose,
        alignment: FaceAligner.Alignment,
        rawMask: ByteArray,
        visibleSkin: ByteArray,
        alphaMask: ByteArray,
    ): Candidate {
        val generated = swapper.swapAligned(alignment.bitmap, convertedEmbedding(reference))
        val relit = try {
            SpatialRelighter.match(
                generated = generated,
                target = alignment.bitmap,
                identityMask = rawMask,
                strength = 0.82f,
            )
        } finally {
            generated.recycle()
        }
        val restored = try {
            MicroDetailRestorer.restore(
                generated = relit,
                target = alignment.bitmap,
                skinVisibleMask = visibleSkin,
                generatedDetailStrength = 0.16f,
                targetTextureStrength = 0.10f,
            )
        } finally {
            relit.recycle()
        }

        val provisional = composite(targetFrame, restored, alphaMask, alignment)
        val evaluation = try {
            qualityEvaluator.evaluate(
                composedFrame = provisional,
                targetFace = targetFace,
                reference = reference,
                targetPose = pose,
                alignedGenerated = restored,
                alignedTarget = alignment.bitmap,
                alpha = alphaMask,
            )
        } finally {
            provisional.recycle()
        }
        return Candidate(reference, restored, evaluation)
    }

    private fun composite(
        targetFrame: Bitmap,
        alignedGenerated: Bitmap,
        alpha: ByteArray,
        alignment: FaceAligner.Alignment,
    ): Bitmap {
        val masked = applyAlpha(alignedGenerated, alpha)
        return try {
            targetFrame.copy(Bitmap.Config.ARGB_8888, true).also { output ->
                Canvas(output).drawBitmap(
                    masked,
                    alignment.alignedToSource,
                    Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG),
                )
            }
        } finally {
            masked.recycle()
        }
    }

    /** Reset temporal and QC history after scene cuts, tracking loss or subject changes. */
    fun resetTemporal() {
        temporal.reset()
        qualityEvaluator.resetTemporal()
    }

    private fun convertedEmbedding(reference: IdentityReference): FloatArray {
        val key = reference.uri.toString()
        return convertedEmbeddingCache[key]?.copyOf() ?: run {
            val source = decodeSoftware(reference.uri)
            try {
                val face = sourceAnalyzer.analyze(source)
                    ?: error("No source face detected for selected Identity Vault reference")
                val rawArcFace = arcFace.encode(source, face)
                converter.convert(rawArcFace).also { converted ->
                    convertedEmbeddingCache[key] = converted.copyOf()
                }
            } finally {
                source.recycle()
            }
        }
    }

    private fun intersectMasks(a: ByteArray, b: ByteArray): ByteArray {
        require(a.size == b.size)
        return ByteArray(a.size) { i ->
            minOf(a[i].toInt() and 0xff, b[i].toInt() and 0xff).toByte()
        }
    }

    /**
     * Keeps only neural identity pixels. Hair, glasses, mouth interior and all
     * foreground occluders remain transparent and therefore remain target footage.
     */
    private fun applyAlpha(generated: Bitmap, alpha: ByteArray): Bitmap {
        require(generated.width * generated.height == alpha.size)
        val pixels = IntArray(alpha.size)
        generated.getPixels(
            pixels,
            0,
            generated.width,
            0,
            0,
            generated.width,
            generated.height,
        )
        for (i in pixels.indices) {
            val a = alpha[i].toInt() and 0xff
            pixels[i] = (a shl 24) or (pixels[i] and 0x00ffffff)
        }
        return Bitmap.createBitmap(
            pixels,
            generated.width,
            generated.height,
            Bitmap.Config.ARGB_8888,
        )
    }

    private fun decodeSoftware(uri: Uri): Bitmap {
        val source = ImageDecoder.createSource(context.contentResolver, uri)
        return ImageDecoder.decodeBitmap(source) { decoder, _, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            decoder.isMutableRequired = false
        }
    }

    override fun close() {
        temporal.close()
        qualityEvaluator.close()
        sourceAnalyzer.close()
        occluder.close()
        parser.close()
        swapper.close()
        converter.close()
        arcFace.close()
        convertedEmbeddingCache.clear()
    }
}
