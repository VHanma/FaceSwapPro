package org.vaan.faceswap.v2.engine

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.graphics.Bitmap
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** Exact ArcFace W600K-R50 embedding family used by the SimSwap converter. */
class ArcFaceR50Encoder(modelFile: File) : AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val session = env.createSession(modelFile.absolutePath, OrtSession.SessionOptions())
    private val inputName = session.inputNames.first()

    fun encode(bitmap: Bitmap, face: TrackerManager.TrackedFace): FloatArray {
        val points = FaceAligner.fivePoints(face.landmarks, bitmap.width, bitmap.height)
        val aligned = FaceAligner.align(
            bitmap = bitmap,
            points = points,
            template = FaceAligner.Template.ARC_FACE_112_V2,
            width = 112,
        ).bitmap
        return try {
            encodeAligned112(aligned)
        } finally {
            aligned.recycle()
        }
    }

    fun encodeAligned112(bitmap: Bitmap): FloatArray {
        val inputBitmap = if (bitmap.width == 112 && bitmap.height == 112) {
            bitmap
        } else Bitmap.createScaledBitmap(bitmap, 112, 112, true)

        val pixels = IntArray(112 * 112)
        inputBitmap.getPixels(pixels, 0, 112, 0, 0, 112, 112)
        val floats = ByteBuffer.allocateDirect(3 * 112 * 112 * Float.SIZE_BYTES)
            .order(ByteOrder.nativeOrder())
            .asFloatBuffer()

        // FaceFusion/InsightFace contract: RGB, value / 127.5 - 1, NCHW.
        for (channel in 0 until 3) {
            for (pixel in pixels) {
                val value = when (channel) {
                    0 -> (pixel shr 16) and 0xff
                    1 -> (pixel shr 8) and 0xff
                    else -> pixel and 0xff
                }
                floats.put(value / 127.5f - 1f)
            }
        }
        floats.rewind()

        val tensor = OnnxTensor.createTensor(env, floats, longArrayOf(1, 3, 112, 112))
        val result = tensor.use { input ->
            session.run(mapOf(inputName to input)).use { outputs ->
                val buffer = (outputs[0] as OnnxTensor).floatBuffer
                FloatArray(buffer.remaining()).also { buffer.get(it) }
            }
        }
        if (inputBitmap !== bitmap) inputBitmap.recycle()
        return result
    }

    override fun close() {
        session.close()
    }
}
