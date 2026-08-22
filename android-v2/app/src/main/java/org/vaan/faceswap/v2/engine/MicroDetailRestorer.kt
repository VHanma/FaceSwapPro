package org.vaan.faceswap.v2.engine

import android.graphics.Bitmap

/**
 * Lightweight post-synthesis micro-detail restoration.
 *
 * Operates only on semantic skin that is also visible through the occlusion gate.
 * It sharpens the neural face's own fine detail and transfers only a restrained
 * high-frequency residual from the real shot. Low/mid-frequency target geometry
 * never enters the generated face, so identity structure stays with the swapper.
 */
object MicroDetailRestorer {
    private const val RADIUS = 2

    fun restore(
        generated: Bitmap,
        target: Bitmap,
        skinVisibleMask: ByteArray,
        generatedDetailStrength: Float = 0.16f,
        targetTextureStrength: Float = 0.10f,
    ): Bitmap {
        require(generated.width == target.width && generated.height == target.height)
        val width = generated.width
        val height = generated.height
        require(skinVisibleMask.size == width * height)

        val generatedPixels = IntArray(width * height)
        val targetPixels = IntArray(width * height)
        generated.getPixels(generatedPixels, 0, width, 0, 0, width, height)
        target.getPixels(targetPixels, 0, width, 0, 0, width, height)

        val gR = integral(generatedPixels, width, height, 16)
        val gG = integral(generatedPixels, width, height, 8)
        val gB = integral(generatedPixels, width, height, 0)
        val tR = integral(targetPixels, width, height, 16)
        val tG = integral(targetPixels, width, height, 8)
        val tB = integral(targetPixels, width, height, 0)
        val out = generatedPixels.copyOf()

        for (y in 0 until height) {
            val y0 = (y - RADIUS).coerceAtLeast(0)
            val y1 = (y + RADIUS + 1).coerceAtMost(height)
            for (x in 0 until width) {
                val i = y * width + x
                val mask = (skinVisibleMask[i].toInt() and 0xff) / 255f
                if (mask <= 0f) continue
                val x0 = (x - RADIUS).coerceAtLeast(0)
                val x1 = (x + RADIUS + 1).coerceAtMost(width)
                val area = (x1 - x0) * (y1 - y0)

                val gp = generatedPixels[i]
                val tp = targetPixels[i]
                val gr = (gp shr 16) and 0xff
                val gg = (gp shr 8) and 0xff
                val gb = gp and 0xff
                val tr = (tp shr 16) and 0xff
                val tg = (tp shr 8) and 0xff
                val tb = tp and 0xff

                val grMean = boxMean(gR, width, x0, y0, x1, y1, area)
                val ggMean = boxMean(gG, width, x0, y0, x1, y1, area)
                val gbMean = boxMean(gB, width, x0, y0, x1, y1, area)
                val trMean = boxMean(tR, width, x0, y0, x1, y1, area)
                val tgMean = boxMean(tG, width, x0, y0, x1, y1, area)
                val tbMean = boxMean(tB, width, x0, y0, x1, y1, area)

                fun enhance(g: Int, gMean: Float, t: Int, tMean: Float): Int {
                    val ownDetail = (g - gMean).coerceIn(-26f, 26f)
                    val targetMicro = (t - tMean).coerceIn(-18f, 18f)
                    val delta = ownDetail * generatedDetailStrength + targetMicro * targetTextureStrength
                    return (g + delta * mask + 0.5f).toInt().coerceIn(0, 255)
                }

                val r = enhance(gr, grMean, tr, trMean)
                val g = enhance(gg, ggMean, tg, tgMean)
                val b = enhance(gb, gbMean, tb, tbMean)
                out[i] = (0xff shl 24) or (r shl 16) or (g shl 8) or b
            }
        }
        return Bitmap.createBitmap(out, width, height, Bitmap.Config.ARGB_8888)
    }

    /** Integral image with one-pixel top/left padding. */
    private fun integral(pixels: IntArray, width: Int, height: Int, shift: Int): IntArray {
        val stride = width + 1
        val sum = IntArray((width + 1) * (height + 1))
        for (y in 0 until height) {
            var row = 0
            for (x in 0 until width) {
                row += (pixels[y * width + x] shr shift) and 0xff
                val index = (y + 1) * stride + (x + 1)
                sum[index] = sum[y * stride + (x + 1)] + row
            }
        }
        return sum
    }

    private fun boxMean(
        integral: IntArray,
        width: Int,
        x0: Int,
        y0: Int,
        x1: Int,
        y1: Int,
        area: Int,
    ): Float {
        val stride = width + 1
        val sum = integral[y1 * stride + x1] -
            integral[y0 * stride + x1] -
            integral[y1 * stride + x0] +
            integral[y0 * stride + x0]
        return sum.toFloat() / area.coerceAtLeast(1)
    }
}
