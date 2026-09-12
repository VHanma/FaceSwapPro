package org.vaan.faceswap.v2.engine

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.graphics.Bitmap
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import kotlin.math.roundToInt

/** Native-resolution 512px SimSwap ONNX inference. Input/output remain RGB internally. */
class SimSwap512Engine(modelFile: File) : AutoCloseable, SwapEngine {
    private val env = OrtEnvironment.getEnvironment()
    private val session = env.createSession(modelFile.absolutePath, OrtSession.SessionOptions())
    private val sourceInputName = session.inputNames.firstOrNull { it.equals("source", ignoreCase = true) }
        ?: session.inputNames.first()
    private val targetInputName = session.inputNames.firstOrNull { it.equals("target", ignoreCase = true) }
        ?: session.inputNames.firstOrNull { it != sourceInputName }
        ?: error("SimSwap model does not expose a target input")

    override fun isAvailable(): Boolean = true
    override fun modelName(): String = "SimSwap 512 Movie Research"

    fun swapAligned(
        targetAligned512: Bitmap,
        convertedSourceEmbedding: FloatArray,
    ): Bitmap {
        require(convertedSourceEmbedding.isNotEmpty()) { "Converted source embedding is empty" }
        val target = if (targetAligned512.width == SIZE && targetAligned512.height == SIZE) {
            targetAligned512
        } else {
            Bitmap.createScaledBitmap(targetAligned512, SIZE, SIZE, true)
        }

        val targetTensor = createTargetTensor(target)
        val sourceTensor = OnnxTensor.createTensor(
            env,
            FloatBuffer.wrap(convertedSourceEmbedding),
            longArrayOf(1, convertedSourceEmbedding.size.toLong()),
        )

        val output = try {
            session.run(
                mapOf(
                    sourceInputName to sourceTensor,
                    targetInputName to targetTensor,
                )
            ).use { result ->
                tensorToBitmap(result[0] as OnnxTensor)
            }
        } finally {
            sourceTensor.close()
            targetTensor.close()
            if (target !== targetAligned512) target.recycle()
        }
        return output
    }

    private fun createTargetTensor(bitmap: Bitmap): OnnxTensor {
        val pixels = IntArray(SIZE * SIZE)
        bitmap.getPixels(pixels, 0, SIZE, 0, 0, SIZE, SIZE)
        val floats = ByteBuffer.allocateDirect(3 * SIZE * SIZE * Float.SIZE_BYTES)
            .order(ByteOrder.nativeOrder())
            .asFloatBuffer()

        // Current 512 SimSwap contract: RGB / 255, NCHW, no additional normalization.
        for (channel in 0 until 3) {
            for (pixel in pixels) {
                val value = when (channel) {
                    0 -> (pixel shr 16) and 0xff
                    1 -> (pixel shr 8) and 0xff
                    else -> pixel and 0xff
                }
                floats.put(value / 255.0f)
            }
        }
        floats.rewind()
        return OnnxTensor.createTensor(
            env,
            floats,
            longArrayOf(1, 3, SIZE.toLong(), SIZE.toLong()),
        )
    }

    private fun tensorToBitmap(tensor: OnnxTensor): Bitmap {
        val shape = tensor.info.shape
        require(shape.size == 4 && shape[0] == 1L && shape[1] == 3L) {
            "Unexpected SimSwap output shape: ${shape.joinToString("x")}"
        }
        val height = shape[2].toInt()
        val width = shape[3].toInt()
        val plane = width * height
        val buffer = tensor.floatBuffer
        require(buffer.remaining() >= plane * 3) { "SimSwap output buffer is truncated" }

        val pixels = IntArray(plane)
        for (i in 0 until plane) {
            val r = (buffer.get(i).coerceIn(0f, 1f) * 255f).roundToInt()
            val g = (buffer.get(plane + i).coerceIn(0f, 1f) * 255f).roundToInt()
            val b = (buffer.get(plane * 2 + i).coerceIn(0f, 1f) * 255f).roundToInt()
            pixels[i] = (0xff shl 24) or (r shl 16) or (g shl 8) or b
        }
        return Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
    }

    override fun close() {
        session.close()
    }

    companion object {
        const val SIZE = 512
    }
}
