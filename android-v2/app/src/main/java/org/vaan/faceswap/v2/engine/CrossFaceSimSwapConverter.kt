package org.vaan.faceswap.v2.engine

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File
import java.nio.FloatBuffer
import kotlin.math.sqrt

/** Converts raw ArcFace W600K-R50 identity features into SimSwap identity space. */
class CrossFaceSimSwapConverter(modelFile: File) : AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val session = env.createSession(modelFile.absolutePath, OrtSession.SessionOptions())
    private val inputName = session.inputNames.first()

    fun convert(rawArcFaceEmbedding: FloatArray): FloatArray {
        require(rawArcFaceEmbedding.isNotEmpty()) { "ArcFace embedding is empty" }
        val inputBuffer = FloatBuffer.wrap(rawArcFaceEmbedding)
        val tensor = OnnxTensor.createTensor(
            env,
            inputBuffer,
            longArrayOf(1, rawArcFaceEmbedding.size.toLong()),
        )
        val converted = tensor.use { input ->
            session.run(mapOf(inputName to input)).use { outputs ->
                val buffer = (outputs[0] as OnnxTensor).floatBuffer
                FloatArray(buffer.remaining()).also { buffer.get(it) }
            }
        }
        return l2Normalize(converted)
    }

    private fun l2Normalize(values: FloatArray): FloatArray {
        var sum = 0.0
        for (value in values) sum += value * value
        val norm = sqrt(sum).coerceAtLeast(1e-12)
        return FloatArray(values.size) { index -> (values[index] / norm).toFloat() }
    }

    override fun close() {
        session.close()
    }
}
