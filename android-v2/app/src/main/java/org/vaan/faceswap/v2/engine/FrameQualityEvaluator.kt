package org.vaan.faceswap.v2.engine

import android.content.Context
import android.graphics.Bitmap
import kotlin.math.abs

/**
 * Per-frame Movie quality gate.
 *
 * The scorer intentionally uses independent evidence from the lightweight EdgeFace
 * model rather than trusting the swapper's own latent. A bad synthesis therefore
 * cannot give itself a passing grade.
 */
class FrameQualityEvaluator(
    context: Context,
    private val vault: DefaultIdentityVault,
) : AutoCloseable {

    data class Candidate(
        val quality: FrameQuality,
        val embedding: FloatArray,
    )

    private val encoder = EdgeFaceEncoder(context)
    private var previousAcceptedEmbedding: FloatArray? = null

    fun evaluate(
        composedFrame: Bitmap,
        targetFace: TrackerManager.TrackedFace,
        reference: IdentityReference,
        targetPose: FacePoseQuality.Pose,
        alignedGenerated: Bitmap,
        alignedTarget: Bitmap,
        alpha: ByteArray,
    ): Candidate {
        val renderedEmbedding = encoder.encode(composedFrame, targetFace)
        val fused = vault.fusedEmbedding()
        val identityCosine = if (fused.size == renderedEmbedding.size && fused.isNotEmpty()) {
            encoder.cosineSimilarity(renderedEmbedding, fused)
        } else 0f

        val identity = normalize(identityCosine, low = 0.05f, high = 0.60f)
        val geometry = geometryScore(reference, targetPose)
        val mask = maskScore(alpha)
        val temporal = previousAcceptedEmbedding?.let { previous ->
            if (previous.size == renderedEmbedding.size) {
                normalize(encoder.cosineSimilarity(previous, renderedEmbedding), low = 0.18f, high = 0.78f)
            } else 1f
        } ?: 1f
        val lighting = lightingScore(alignedGenerated, alignedTarget, alpha)

        return Candidate(
            quality = FrameQuality(identity, geometry, mask, temporal, lighting),
            embedding = renderedEmbedding,
        )
    }

    fun accept(candidate: Candidate) {
        previousAcceptedEmbedding = candidate.embedding.copyOf()
    }

    fun resetTemporal() {
        previousAcceptedEmbedding = null
    }

    private fun geometryScore(reference: IdentityReference, pose: FacePoseQuality.Pose): Float {
        val yaw = 1f - (abs(reference.yaw - pose.yaw) / 90f).coerceIn(0f, 1f)
        val pitch = 1f - (abs(reference.pitch - pose.pitch) / 65f).coerceIn(0f, 1f)
        val roll = 1f - (abs(reference.roll - pose.roll) / 90f).coerceIn(0f, 1f)
        return (yaw * 0.65f + pitch * 0.25f + roll * 0.10f).coerceIn(0f, 1f)
    }

    private fun maskScore(alpha: ByteArray): Float {
        if (alpha.isEmpty()) return 0f
        var visible = 0
        var soft = 0
        var sum = 0L
        for (value in alpha) {
            val a = value.toInt() and 0xff
            if (a > 16) visible++
            if (a in 17..238) soft++
            sum += a
        }
        val visibleRatio = visible.toFloat() / alpha.size
        val coverage = (visibleRatio / 0.12f).coerceIn(0f, 1f)
        val softness = (soft.toFloat() / alpha.size / 0.025f).coerceIn(0f, 1f)
        val meanAlpha = sum.toFloat() / (alpha.size * 255f)
        val density = (meanAlpha / 0.10f).coerceIn(0f, 1f)
        return coverage * 0.55f + softness * 0.20f + density * 0.25f
    }

    private fun lightingScore(generated: Bitmap, target: Bitmap, alpha: ByteArray): Float {
        if (generated.width != target.width || generated.height != target.height) return 0f
        if (alpha.size != generated.width * generated.height) return 0f
        val gp = IntArray(alpha.size)
        val tp = IntArray(alpha.size)
        generated.getPixels(gp, 0, generated.width, 0, 0, generated.width, generated.height)
        target.getPixels(tp, 0, target.width, 0, 0, target.width, target.height)

        var error = 0.0
        var count = 0
        // Sparse sampling is enough for low-frequency illumination validation.
        for (y in 0 until generated.height step 4) {
            for (x in 0 until generated.width step 4) {
                val i = y * generated.width + x
                if ((alpha[i].toInt() and 0xff) < 48) continue
                val a = gp[i]
                val b = tp[i]
                error += abs(((a shr 16) and 0xff) - ((b shr 16) and 0xff))
                error += abs(((a shr 8) and 0xff) - ((b shr 8) and 0xff))
                error += abs((a and 0xff) - (b and 0xff))
                count += 3
            }
        }
        if (count == 0) return 0.7f // heavy occlusion is not itself a lighting failure
        val mae = error / count
        return (1.0 - mae / 95.0).toFloat().coerceIn(0f, 1f)
    }

    private fun normalize(value: Float, low: Float, high: Float): Float =
        ((value - low) / (high - low)).coerceIn(0f, 1f)

    override fun close() {
        encoder.close()
        previousAcceptedEmbedding = null
    }
}
