package org.vaan.faceswap.v2.engine

import android.graphics.Bitmap
import kotlin.math.abs
import kotlin.math.sqrt

/**
 * Matches the inserted face's local sharpness/softness to the target camera crop.
 *
 * A synthetic face can be technically detailed yet still look pasted-on when it
 * is much sharper than the lens, motion blur or compression around it. This stage
 * estimates masked high-frequency energy and gently blurs or unsharpens the
 * generated crop toward the target instead of applying a fixed beauty filter.
 */
object CameraCharacterMatcher {
    fun match(
        generated: Bitmap,
        target: Bitmap,
        mask: ByteArray,
        maxStrength: Float = 0.42f,
    ): Bitmap {
        require(generated.width == target.width && generated.height == target.height)
        require(mask.size == generated.width * generated.height)
        val width = generated.width
        val height = generated.height
        val gp = IntArray(width * height)
        val tp = IntArray(width * height)
        generated.getPixels(gp, 0, width, 0, 0, width, height)
        target.getPixels(tp, 0, width, 0, 0, width, height)

        val gEnergy = edgeEnergy(gp, mask, width, height)
        val tEnergy = edgeEnergy(tp, mask, width, height)
        if (gEnergy <= 1e-3 || tEnergy <= 1e-3) {
            return generated.copy(Bitmap.Config.ARGB_8888, false)
        }

        val ratio = (tEnergy / gEnergy).coerceIn(0.45, 1.65)
        val amount = (abs(1.0 - ratio) * 0.75).toFloat().coerceIn(0f, maxStrength)
        if (amount < 0.025f) return generated.copy(Bitmap.Config.ARGB_8888, false)

        val blurred = boxBlur3x3(gp, width, height)
        val out = gp.copyOf()
        for (i in out.indices) {
            val m = (mask[i].toInt() and 0xff) / 255f
            if (m <= 0f) continue
            val weight = amount * m
            val original = gp[i]
            val low = blurred[i]

            fun channel(shift: Int): Int {
                val o = (original shr shift) and 0xff
                val b = (low shr shift) and 0xff
                val v = if (ratio < 1.0) {
                    // Generated face is too crisp for the shot.
                    o + (b - o) * weight
                } else {
                    // Generated face is softer. Mild unsharp mask, deliberately
                    // capped to avoid haloing eyelashes, lips and mask boundaries.
                    o + (o - b) * weight * 0.55f
                }
                return (v + 0.5f).toInt().coerceIn(0, 255)
            }

            val r = channel(16)
            val g = channel(8)
            val b = channel(0)
            out[i] = (0xff shl 24) or (r shl 16) or (g shl 8) or b
        }
        return Bitmap.createBitmap(out, width, height, Bitmap.Config.ARGB_8888)
    }

    private fun edgeEnergy(
        pixels: IntArray,
        mask: ByteArray,
        width: Int,
        height: Int,
    ): Double {
        var sumSq = 0.0
        var count = 0
        for (y in 1 until height - 1 step 2) {
            for (x in 1 until width - 1 step 2) {
                val i = y * width + x
                if ((mask[i].toInt() and 0xff) < 96) continue
                val center = luma(pixels[i])
                val lap = 4 * center -
                    luma(pixels[i - 1]) -
                    luma(pixels[i + 1]) -
                    luma(pixels[i - width]) -
                    luma(pixels[i + width])
                sumSq += lap.toDouble() * lap.toDouble()
                count++
            }
        }
        return if (count == 0) 0.0 else sqrt(sumSq / count)
    }

    private fun boxBlur3x3(pixels: IntArray, width: Int, height: Int): IntArray {
        val out = pixels.copyOf()
        for (y in 1 until height - 1) {
            for (x in 1 until width - 1) {
                var rs = 0
                var gs = 0
                var bs = 0
                for (dy in -1..1) {
                    for (dx in -1..1) {
                        val p = pixels[(y + dy) * width + x + dx]
                        rs += (p shr 16) and 0xff
                        gs += (p shr 8) and 0xff
                        bs += p and 0xff
                    }
                }
                out[y * width + x] = (0xff shl 24) or
                    ((rs / 9) shl 16) or
                    ((gs / 9) shl 8) or
                    (bs / 9)
            }
        }
        return out
    }

    private fun luma(pixel: Int): Int {
        val r = (pixel shr 16) and 0xff
        val g = (pixel shr 8) and 0xff
        val b = pixel and 0xff
        return (54 * r + 183 * g + 19 * b) shr 8
    }
}
