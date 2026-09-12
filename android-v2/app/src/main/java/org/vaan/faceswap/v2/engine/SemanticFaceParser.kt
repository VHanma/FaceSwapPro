package org.vaan.faceswap.v2.engine

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import android.content.Context
import android.graphics.Bitmap
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * 19-class CelebAMask-HQ BiSeNet parser.
 *
 * Model contract follows yakhyo/face-parsing ResNet18 ONNX:
 * input  [1,3,512,512] RGB float32, ImageNet normalization
 * output [1,19,512,512] logits
 */
class SemanticFaceParser(
    private val context: Context,
    assetName: String = "models/face_parser_resnet18.onnx",
) : AutoCloseable {

    enum class Region(val id: Int) {
        BACKGROUND(0),
        SKIN(1),
        LEFT_BROW(2),
        RIGHT_BROW(3),
        LEFT_EYE(4),
        RIGHT_EYE(5),
        EYEGLASSES(6),
        LEFT_EAR(7),
        RIGHT_EAR(8),
        EARRING(9),
        NOSE(10),
        MOUTH(11),
        UPPER_LIP(12),
        LOWER_LIP(13),
        NECK(14),
        NECKLACE(15),
        CLOTH(16),
        HAIR(17),
        HAT(18),
    }

    data class SemanticMask(
        val width: Int,
        val height: Int,
        val labels: ByteArray,
    ) {
        init {
            require(labels.size == width * height)
        }

        fun labelAt(x: Int, y: Int): Int = labels[y * width + x].toInt() and 0xff

        fun binaryMask(vararg regions: Region): ByteArray {
            val accepted = BooleanArray(19)
            regions.forEach { accepted[it.id] = true }
            return ByteArray(labels.size) { i ->
                if (accepted[labels[i].toInt() and 0xff]) 0xff.toByte() else 0
            }
        }

        /** Regions that normally remain from the target in front of the swapped identity. */
        fun foregroundProtectionMask(): ByteArray = binaryMask(
            Region.HAIR,
            Region.HAT,
            Region.EYEGLASSES,
            Region.EARRING,
        )

        /** Identity-bearing area. Mouth interior is intentionally excluded. */
        fun identityCompositeMask(): ByteArray = binaryMask(
            Region.SKIN,
            Region.LEFT_BROW,
            Region.RIGHT_BROW,
            Region.LEFT_EYE,
            Region.RIGHT_EYE,
            Region.NOSE,
            Region.UPPER_LIP,
            Region.LOWER_LIP,
        )

        fun mouthInteriorMask(): ByteArray = binaryMask(Region.MOUTH)
    }

    private val env = OrtEnvironment.getEnvironment()
    private val session: OrtSession
    private val inputName: String

    init {
        val modelFile = copyAssetModel(assetName)
        session = env.createSession(modelFile.absolutePath, OrtSession.SessionOptions())
        inputName = session.inputNames.first()
    }

    fun parse(bitmap: Bitmap): SemanticMask {
        val scaled = if (bitmap.width == INPUT_SIZE && bitmap.height == INPUT_SIZE) {
            bitmap
        } else {
            Bitmap.createScaledBitmap(bitmap, INPUT_SIZE, INPUT_SIZE, true)
        }

        val pixels = IntArray(INPUT_SIZE * INPUT_SIZE)
        scaled.getPixels(pixels, 0, INPUT_SIZE, 0, 0, INPUT_SIZE, INPUT_SIZE)

        val byteBuffer = ByteBuffer.allocateDirect(INPUT_FLOATS * Float.SIZE_BYTES)
            .order(ByteOrder.nativeOrder())
        val floats = byteBuffer.asFloatBuffer()

        // NCHW layout. Android pixels are ARGB; model expects normalized RGB.
        for (channel in 0 until 3) {
            val mean = MEAN[channel]
            val std = STD[channel]
            for (pixel in pixels) {
                val component = when (channel) {
                    0 -> (pixel shr 16) and 0xff
                    1 -> (pixel shr 8) and 0xff
                    else -> pixel and 0xff
                }
                floats.put((component / 255.0f - mean) / std)
            }
        }
        floats.rewind()

        val tensor = OnnxTensor.createTensor(
            env,
            floats,
            longArrayOf(1, 3, INPUT_SIZE.toLong(), INPUT_SIZE.toLong()),
        )

        val labels512: ByteArray
        tensor.use { input ->
            session.run(mapOf(inputName to input)).use { result ->
                val output = result[0] as OnnxTensor
                val logits = output.floatBuffer
                labels512 = argmaxNchw(logits)
            }
        }

        if (scaled !== bitmap) scaled.recycle()

        // Keep the neural parser at its native resolution. The compositor maps this
        // crop mask back to the tracked face rectangle using nearest-neighbor sampling.
        return SemanticMask(INPUT_SIZE, INPUT_SIZE, labels512)
    }

    private fun argmaxNchw(logits: java.nio.FloatBuffer): ByteArray {
        val plane = INPUT_SIZE * INPUT_SIZE
        require(logits.remaining() >= NUM_CLASSES * plane) {
            "Unexpected face-parser output: ${logits.remaining()} floats"
        }
        val output = ByteArray(plane)
        val best = FloatArray(plane) { Float.NEGATIVE_INFINITY }

        for (clazz in 0 until NUM_CLASSES) {
            val base = clazz * plane
            for (i in 0 until plane) {
                val value = logits.get(base + i)
                if (value > best[i]) {
                    best[i] = value
                    output[i] = clazz.toByte()
                }
            }
        }
        return output
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

    companion object {
        const val INPUT_SIZE = 512
        const val NUM_CLASSES = 19
        private const val INPUT_FLOATS = 3 * INPUT_SIZE * INPUT_SIZE
        private val MEAN = floatArrayOf(0.485f, 0.456f, 0.406f)
        private val STD = floatArrayOf(0.229f, 0.224f, 0.225f)
    }
}
