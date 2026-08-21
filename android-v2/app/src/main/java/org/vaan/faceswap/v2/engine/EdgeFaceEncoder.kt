package org.vaan.faceswap.v2.engine

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.sqrt

/** Lightweight 512-D identity encoder for Identity Vault + rendered-frame QC. */
class EdgeFaceEncoder(
    private val context: Context,
    assetName: String = "models/edgeface_xxs.onnx",
) : AutoCloseable {

    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession
    private val inputName: String

    init {
        val model = copyAssetModel(assetName)
        session = env.createSession(model.absolutePath, OrtSession.SessionOptions())
        inputName = session.inputNames.first()
    }

    fun encodeAligned112(aligned: Bitmap): FloatArray {
        val bitmap = if (aligned.width == 112 && aligned.height == 112) {
            aligned
        } else {
            Bitmap.createScaledBitmap(aligned, 112, 112, true)
        }

        val pixels = IntArray(112 * 112)
        bitmap.getPixels(pixels, 0, 112, 0, 0, 112, 112)
        val buffer = ByteBuffer.allocateDirect(3 * 112 * 112 * Float.SIZE_BYTES)
            .order(ByteOrder.nativeOrder())
            .asFloatBuffer()

        // EdgeFace reference preprocessing: RGB and (value - 127.5) / 127.5.
        for (channel in 0 until 3) {
            for (pixel in pixels) {
                val value = when (channel) {
                    0 -> (pixel shr 16) and 0xff
                    1 -> (pixel shr 8) and 0xff
                    else -> pixel and 0xff
                }
                buffer.put((value - 127.5f) / 127.5f)
            }
        }
        buffer.rewind()

        val input = OnnxTensor.createTensor(env, buffer, longArrayOf(1, 3, 112, 112))
        val raw = input.use { tensor ->
            session.run(mapOf(inputName to tensor)).use { result ->
                (result[0] as OnnxTensor).floatBuffer.let { output ->
                    FloatArray(output.remaining()).also { output.get(it) }
                }
            }
        }
        if (bitmap !== aligned) bitmap.recycle()
        return l2Normalize(raw)
    }

    fun encode(bitmap: Bitmap, face: TrackerManager.TrackedFace): FloatArray {
        val five = FaceAligner.fivePoints(face.landmarks, bitmap.width, bitmap.height)
        val aligned = FaceAligner.align112(bitmap, five)
        return try {
            encodeAligned112(aligned)
        } finally {
            aligned.recycle()
        }
    }

    fun cosineSimilarity(a: FloatArray, b: FloatArray): Float {
        require(a.size == b.size)
        var dot = 0.0
        for (i in a.indices) dot += a[i] * b[i]
        return dot.toFloat()
    }

    private fun l2Normalize(values: FloatArray): FloatArray {
        var sum = 0.0
        for (v in values) sum += v * v
        val norm = sqrt(sum).coerceAtLeast(1e-12)
        return FloatArray(values.size) { i -> (values[i] / norm).toFloat() }
    }

    private fun copyAssetModel(assetName: String): File {
        val dir = File(context.noBackupFilesDir, "models").apply { mkdirs() }
        val out = File(dir, assetName.substringAfterLast('/'))
        if (!out.exists() || out.length() < 1024L * 1024L) {
            context.assets.open(assetName).use { input ->
                out.outputStream().use { output -> input.copyTo(output, 1024 * 1024) }
            }
        }
        return out
    }

    override fun close() {
        session.close()
    }
}
