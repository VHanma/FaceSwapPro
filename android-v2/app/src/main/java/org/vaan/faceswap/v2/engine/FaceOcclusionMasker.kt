package org.vaan.faceswap.v2.engine

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.graphics.Bitmap
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.floor
import kotlin.math.roundToInt

/**
 * XSeg arbitrary-occlusion gate.
 *
 * The semantic parser knows face parts; XSeg answers the different question:
 * "which pixels of this face crop are actually visible and safe to replace?"
 * This keeps hands, phones, microphones and other crossing foreground objects
 * in front of the synthetic identity.
 */
class FaceOcclusionMasker(modelFile: File) : AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession = env.createSession(modelFile.absolutePath, OrtSession.SessionOptions())
    private val inputName: String = session.inputNames.first()

    fun visibleFaceMask(alignedFace: Bitmap, outputSize: Int = alignedFace.width): ByteArray {
        require(alignedFace.width == alignedFace.height) { "XSeg expects a square aligned face crop" }
        val scaled = if (alignedFace.width == MODEL_SIZE && alignedFace.height == MODEL_SIZE) {
            alignedFace
        } else {
            Bitmap.createScaledBitmap(alignedFace, MODEL_SIZE, MODEL_SIZE, true)
        }
        val pixels = IntArray(MODEL_SIZE * MODEL_SIZE)
        scaled.getPixels(pixels, 0, MODEL_SIZE, 0, 0, MODEL_SIZE, MODEL_SIZE)

        // FaceFusion feeds XSeg an NHWC OpenCV/BGR float image in [0,1].
        val buffer = ByteBuffer.allocateDirect(MODEL_SIZE * MODEL_SIZE * 3 * Float.SIZE_BYTES)
            .order(ByteOrder.nativeOrder())
            .asFloatBuffer()
        for (pixel in pixels) {
            val r = (pixel shr 16) and 0xff
            val g = (pixel shr 8) and 0xff
            val b = pixel and 0xff
            buffer.put(b / 255f)
            buffer.put(g / 255f)
            buffer.put(r / 255f)
        }
        buffer.rewind()

        val raw = OnnxTensor.createTensor(
            env,
            buffer,
            longArrayOf(1, MODEL_SIZE.toLong(), MODEL_SIZE.toLong(), 3),
        ).use { tensor ->
            session.run(mapOf(inputName to tensor)).use { result ->
                val output = result[0] as OnnxTensor
                val floats = output.floatBuffer
                FloatArray(floats.remaining()).also { floats.get(it) }
            }
        }
        if (scaled !== alignedFace) scaled.recycle()
        require(raw.size >= MODEL_SIZE * MODEL_SIZE) {
            "Unexpected XSeg output size ${raw.size}"
        }

        val upsampled = resizeMask(raw, MODEL_SIZE, MODEL_SIZE, outputSize, outputSize)
        val rawBytes = ByteArray(upsampled.size) { i ->
            (upsampled[i].coerceIn(0f, 1f) * 255f + 0.5f).toInt().toByte()
        }
        // Approximate FaceFusion's sigma-5 Gaussian before its 0.5 visibility gate.
        val blurred = MaskFeather.feather(rawBytes, outputSize, outputSize, radius = 5, passes = 2)
        return ByteArray(blurred.size) { i ->
            val v = (blurred[i].toInt() and 0xff) / 255f
            (((v.coerceIn(0.5f, 1f) - 0.5f) * 2f) * 255f)
                .roundToInt()
                .coerceIn(0, 255)
                .toByte()
        }
    }

    private fun resizeMask(
        input: FloatArray,
        inW: Int,
        inH: Int,
        outW: Int,
        outH: Int,
    ): FloatArray {
        if (inW == outW && inH == outH) return input.copyOf(inW * inH)
        val output = FloatArray(outW * outH)
        val scaleX = inW.toFloat() / outW
        val scaleY = inH.toFloat() / outH
        for (y in 0 until outH) {
            val sy = ((y + 0.5f) * scaleY - 0.5f).coerceIn(0f, (inH - 1).toFloat())
            val y0 = floor(sy).toInt()
            val y1 = minOf(inH - 1, y0 + 1)
            val fy = sy - y0
            for (x in 0 until outW) {
                val sx = ((x + 0.5f) * scaleX - 0.5f).coerceIn(0f, (inW - 1).toFloat())
                val x0 = floor(sx).toInt()
                val x1 = minOf(inW - 1, x0 + 1)
                val fx = sx - x0
                val a = input[y0 * inW + x0]
                val b = input[y0 * inW + x1]
                val c = input[y1 * inW + x0]
                val d = input[y1 * inW + x1]
                val top = a + (b - a) * fx
                val bottom = c + (d - c) * fx
                output[y * outW + x] = top + (bottom - top) * fy
            }
        }
        return output
    }

    override fun close() {
        session.close()
    }

    companion object {
        const val MODEL_SIZE = 256
    }
}
