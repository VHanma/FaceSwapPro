package org.vaan.faceswap.v2.engine

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Multi-photo source identity store.
 *
 * Each photo is independently detected/aligned, encoded with EdgeFace and scored.
 * Target frames can retrieve ranked pose references while a weighted fused
 * embedding supplies stable identity evidence for swap QC.
 */
class DefaultIdentityVault(private val context: Context) : IdentityVault {
    private var references: List<IdentityReference> = emptyList()
    private var fused: FloatArray = floatArrayOf()

    override suspend fun build(references: List<Uri>): List<IdentityReference> =
        withContext(Dispatchers.Default) {
            require(references.isNotEmpty()) { "At least one identity source is required" }

            val preliminary = mutableListOf<IdentityReference>()
            SourceFaceAnalyzer(context).use { analyzer ->
                EdgeFaceEncoder(context).use { encoder ->
                    for (uri in references.distinct().take(8)) {
                        val bitmap = decodeSoftware(uri)
                        try {
                            val face = analyzer.analyze(bitmap) ?: continue
                            val pose = FacePoseQuality.estimatePose(face)
                            val sharpness = FacePoseQuality.sharpness(bitmap)
                            val embedding = encoder.encode(bitmap, face)
                            preliminary += IdentityReference(
                                uri = uri,
                                yaw = pose.yaw,
                                pitch = pose.pitch,
                                roll = pose.roll,
                                sharpness = sharpness,
                                embedding = embedding,
                            )
                        } finally {
                            bitmap.recycle()
                        }
                    }
                }
            }

            require(preliminary.isNotEmpty()) { "No usable face was detected in the Identity Vault photos" }

            fused = weightedFusion(preliminary)
            this@DefaultIdentityVault.references = preliminary.map { reference ->
                reference.copy(identityScore = cosine(reference.embedding, fused))
            }
            this@DefaultIdentityVault.references
        }

    override fun bestReference(
        yaw: Float,
        pitch: Float,
        expressionHint: String?,
    ): IdentityReference? = rankedReferences(yaw, pitch).firstOrNull()

    fun rankedReferences(yaw: Float, pitch: Float): List<IdentityReference> =
        references.sortedByDescending { reference -> referenceFit(reference, yaw, pitch) }

    override fun fusedEmbedding(): FloatArray = fused.copyOf()

    fun allReferences(): List<IdentityReference> = references.toList()

    private fun referenceFit(reference: IdentityReference, yaw: Float, pitch: Float): Float {
        val yawFit = 1f - (abs(reference.yaw - yaw) / 90f).coerceIn(0f, 1f)
        val pitchFit = 1f - (abs(reference.pitch - pitch) / 65f).coerceIn(0f, 1f)
        val rollFit = 1f - (abs(reference.roll) / 50f).coerceIn(0f, 1f)
        val poseFit = yawFit * 0.68f + pitchFit * 0.24f + rollFit * 0.08f
        return poseFit * 0.62f + reference.sharpness * 0.23f + reference.identityScore * 0.15f
    }

    private fun weightedFusion(items: List<IdentityReference>): FloatArray {
        val size = items.first().embedding.size
        require(size > 0 && items.all { it.embedding.size == size })
        val combined = DoubleArray(size)
        var weightSum = 0.0

        for (item in items) {
            val frontalBonus = (1f - abs(item.yaw) / 90f).coerceIn(0f, 1f)
            val weight = (0.35f + item.sharpness * 0.45f + frontalBonus * 0.20f).toDouble()
            for (i in 0 until size) combined[i] += item.embedding[i] * weight
            weightSum += weight
        }

        val output = FloatArray(size) { i -> (combined[i] / weightSum.coerceAtLeast(1e-9)).toFloat() }
        var normSq = 0.0
        for (v in output) normSq += v * v
        val norm = sqrt(normSq).coerceAtLeast(1e-12)
        for (i in output.indices) output[i] = (output[i] / norm).toFloat()
        return output
    }

    private fun cosine(a: FloatArray, b: FloatArray): Float {
        if (a.size != b.size || a.isEmpty()) return 0f
        var dot = 0.0
        for (i in a.indices) dot += a[i] * b[i]
        return dot.toFloat().coerceIn(-1f, 1f)
    }

    private fun decodeSoftware(uri: Uri): Bitmap {
        val source = ImageDecoder.createSource(context.contentResolver, uri)
        return ImageDecoder.decodeBitmap(source) { decoder, _, _ ->
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            decoder.isMutableRequired = false
        }
    }
}
