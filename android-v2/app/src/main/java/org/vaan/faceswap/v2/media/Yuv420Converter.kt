package org.vaan.faceswap.v2.media

import android.graphics.Bitmap
import android.graphics.ImageFormat
import android.media.Image
import android.media.MediaCodecInfo
import java.nio.ByteBuffer
import kotlin.math.roundToInt

object Yuv420Converter {

    fun imageToBitmap(image: Image): Bitmap {
        require(image.format == ImageFormat.YUV_420_888) { "Expected YUV_420_888, got ${image.format}" }
        val crop = image.cropRect
        val width = crop.width()
        val height = crop.height()
        val planes = image.planes
        require(planes.size >= 3)

        val yBuffer = planes[0].buffer
        val uBuffer = planes[1].buffer
        val vBuffer = planes[2].buffer
        val yRowStride = planes[0].rowStride
        val yPixelStride = planes[0].pixelStride
        val uRowStride = planes[1].rowStride
        val uPixelStride = planes[1].pixelStride
        val vRowStride = planes[2].rowStride
        val vPixelStride = planes[2].pixelStride

        val pixels = IntArray(width * height)
        val cropLeft = crop.left
        val cropTop = crop.top

        for (y in 0 until height) {
            val sourceY = cropTop + y
            val chromaY = sourceY / 2
            for (x in 0 until width) {
                val sourceX = cropLeft + x
                val chromaX = sourceX / 2
                val yy = yBuffer.get(sourceY * yRowStride + sourceX * yPixelStride).toInt() and 0xff
                val uu = uBuffer.get(chromaY * uRowStride + chromaX * uPixelStride).toInt() and 0xff
                val vv = vBuffer.get(chromaY * vRowStride + chromaX * vPixelStride).toInt() and 0xff

                // Limited-range YUV -> RGB. This intentionally matches the SDR
                // mastering path. Color-standard-aware matrices can be layered on
                // top without changing the decoder plane/stride handling.
                val c = (yy - 16).coerceAtLeast(0)
                val d = uu - 128
                val e = vv - 128
                val r = ((298 * c + 409 * e + 128) shr 8).coerceIn(0, 255)
                val g = ((298 * c - 100 * d - 208 * e + 128) shr 8).coerceIn(0, 255)
                val b = ((298 * c + 516 * d + 128) shr 8).coerceIn(0, 255)
                pixels[y * width + x] = (0xff shl 24) or (r shl 16) or (g shl 8) or b
            }
        }
        return Bitmap.createBitmap(pixels, width, height, Bitmap.Config.ARGB_8888)
    }

    fun bitmapToEncoderBuffer(
        bitmap: Bitmap,
        destination: ByteBuffer,
        colorFormat: Int,
    ): Int {
        val width = bitmap.width
        val height = bitmap.height
        require(width % 2 == 0 && height % 2 == 0) { "YUV420 encoder frames require even dimensions" }
        val frameBytes = width * height * 3 / 2
        require(destination.capacity() >= frameBytes) {
            "Encoder input buffer too small: ${destination.capacity()} < $frameBytes"
        }

        val pixels = IntArray(width * height)
        bitmap.getPixels(pixels, 0, width, 0, 0, width, height)
        destination.clear()

        val ySize = width * height
        val chromaSize = ySize / 4
        val yPlane = ByteArray(ySize)
        val uPlane = ByteArray(chromaSize)
        val vPlane = ByteArray(chromaSize)

        // Luma is full-resolution.
        for (y in 0 until height) {
            for (x in 0 until width) {
                val pixel = pixels[y * width + x]
                val r = (pixel shr 16) and 0xff
                val g = (pixel shr 8) and 0xff
                val b = pixel and 0xff
                val yValue = (16f + 0.257f * r + 0.504f * g + 0.098f * b)
                    .roundToInt()
                    .coerceIn(16, 235)
                yPlane[y * width + x] = yValue.toByte()
            }
        }

        // 4:2:0 chroma must represent the whole 2x2 block. Sampling only the
        // top-left pixel creates colored stair-steps around lips, hair and mask
        // edges, so average all four RGB pixels before computing U/V.
        var uvIndex = 0
        for (y in 0 until height step 2) {
            for (x in 0 until width step 2) {
                var rSum = 0
                var gSum = 0
                var bSum = 0
                for (dy in 0..1) {
                    for (dx in 0..1) {
                        val pixel = pixels[(y + dy) * width + (x + dx)]
                        rSum += (pixel shr 16) and 0xff
                        gSum += (pixel shr 8) and 0xff
                        bSum += pixel and 0xff
                    }
                }
                val r = rSum * 0.25f
                val g = gSum * 0.25f
                val b = bSum * 0.25f
                val uValue = (128f - 0.148f * r - 0.291f * g + 0.439f * b)
                    .roundToInt()
                    .coerceIn(16, 240)
                val vValue = (128f + 0.439f * r - 0.368f * g - 0.071f * b)
                    .roundToInt()
                    .coerceIn(16, 240)
                uPlane[uvIndex] = uValue.toByte()
                vPlane[uvIndex] = vValue.toByte()
                uvIndex++
            }
        }

        destination.put(yPlane)
        when (colorFormat) {
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar,
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420PackedSemiPlanar -> {
                for (i in 0 until chromaSize) {
                    destination.put(uPlane[i])
                    destination.put(vPlane[i])
                }
            }
            else -> {
                // Planar I420 is the portable byte-buffer layout for planar/flexible YUV420.
                destination.put(uPlane)
                destination.put(vPlane)
            }
        }
        destination.flip()
        return frameBytes
    }

    fun chooseEncoderColorFormat(formats: IntArray): Int {
        val preferred = intArrayOf(
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar,
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar,
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible,
        )
        return preferred.firstOrNull { wanted -> formats.contains(wanted) }
            ?: error("No supported YUV420 byte-buffer encoder format: ${formats.joinToString()}")
    }
}
