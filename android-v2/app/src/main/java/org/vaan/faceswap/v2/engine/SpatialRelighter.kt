package org.vaan.faceswap.v2.engine

import android.graphics.Bitmap
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Low-frequency spatial color/illumination transfer in aligned face space.
 *
 * Global histogram matching makes faces look pasted-on when the shot has one-sided
 * light or colored bounce. This samples local target/generated statistics on a
 * coarse grid, smooths the correction field, then bilinearly applies it only to
 * semantic identity pixels. High-frequency source identity detail is left intact.
 */
object SpatialRelighter {
    private const val CELL = 48

    fun match(
        generated: Bitmap,
        target: Bitmap,
        identityMask: ByteArray,
        strength: Float = 0.82f,
    ): Bitmap {
        require(generated.width == target.width && generated.height == target.height)
        require(identityMask.size == generated.width * generated.height)
        val width = generated.width
        val height = generated.height
        val sourcePixels = IntArray(width * height)
        val targetPixels = IntArray(width * height)
        generated.getPixels(sourcePixels, 0, width, 0, 0, width, height)
        target.getPixels(targetPixels, 0, width, 0, 0, width, height)

        val gridW = (width + CELL - 1) / CELL
        val gridH = (height + CELL - 1) / CELL
        val fields = Array(gridW * gridH) { Correction() }

        for (gy in 0 until gridH) {
            for (gx in 0 until gridW) {
                val x0 = gx * CELL
                val y0 = gy * CELL
                val x1 = min(width, x0 + CELL)
                val y1 = min(height, y0 + CELL)
                fields[gy * gridW + gx] = statistics(
                    sourcePixels,
                    targetPixels,
                    identityMask,
                    width,
                    x0,
                    y0,
                    x1,
                    y1,
                )
            }
        }

        // Smooth one cell outward so block boundaries cannot show through skin.
        val smooth = Array(fields.size) { Correction() }
        for (gy in 0 until gridH) {
            for (gx in 0 until gridW) {
                var weightSum = 0f
                var gr = 0f; var gg = 0f; var gb = 0f
                var or = 0f; var og = 0f; var ob = 0f
                for (dy in -1..1) {
                    for (dx in -1..1) {
                        val nx = gx + dx
                        val ny = gy + dy
                        if (nx !in 0 until gridW || ny !in 0 until gridH) continue
                        val c = fields[ny * gridW + nx]
                        if (!c.valid) continue
                        val w = if (dx == 0 && dy == 0) 2f else 1f
                        gr += c.gainR * w; gg += c.gainG * w; gb += c.gainB * w
                        or += c.offsetR * w; og += c.offsetG * w; ob += c.offsetB * w
                        weightSum += w
                    }
                }
                smooth[gy * gridW + gx] = if (weightSum > 0f) {
                    Correction(
                        gainR = gr / weightSum,
                        gainG = gg / weightSum,
                        gainB = gb / weightSum,
                        offsetR = or / weightSum,
                        offsetG = og / weightSum,
                        offsetB = ob / weightSum,
                        valid = true,
                    )
                } else Correction()
            }
        }

        val output = sourcePixels.copyOf()
        val amount = strength.coerceIn(0f, 1f)
        for (y in 0 until height) {
            for (x in 0 until width) {
                val index = y * width + x
                val mask = (identityMask[index].toInt() and 0xff) / 255f
                if (mask <= 0f) continue

                val gridX = x.toFloat() / CELL
                val gridY = y.toFloat() / CELL
                val x0 = floor(gridX).toInt().coerceIn(0, gridW - 1)
                val y0 = floor(gridY).toInt().coerceIn(0, gridH - 1)
                val x1 = min(gridW - 1, x0 + 1)
                val y1 = min(gridH - 1, y0 + 1)
                val fx = (gridX - x0).coerceIn(0f, 1f)
                val fy = (gridY - y0).coerceIn(0f, 1f)
                val c = interpolate(
                    smooth[y0 * gridW + x0],
                    smooth[y0 * gridW + x1],
                    smooth[y1 * gridW + x0],
                    smooth[y1 * gridW + x1],
                    fx,
                    fy,
                )
                if (!c.valid) continue

                val p = sourcePixels[index]
                val r = (p shr 16) and 0xff
                val g = (p shr 8) and 0xff
                val b = p and 0xff
                val blend = amount * mask
                val correctedR = (r * c.gainR + c.offsetR).coerceIn(0f, 255f)
                val correctedG = (g * c.gainG + c.offsetG).coerceIn(0f, 255f)
                val correctedB = (b * c.gainB + c.offsetB).coerceIn(0f, 255f)
                val outR = (r + (correctedR - r) * blend + 0.5f).toInt().coerceIn(0, 255)
                val outG = (g + (correctedG - g) * blend + 0.5f).toInt().coerceIn(0, 255)
                val outB = (b + (correctedB - b) * blend + 0.5f).toInt().coerceIn(0, 255)
                output[index] = (0xff shl 24) or (outR shl 16) or (outG shl 8) or outB
            }
        }

        return Bitmap.createBitmap(output, width, height, Bitmap.Config.ARGB_8888)
    }

    private data class Correction(
        val gainR: Float = 1f,
        val gainG: Float = 1f,
        val gainB: Float = 1f,
        val offsetR: Float = 0f,
        val offsetG: Float = 0f,
        val offsetB: Float = 0f,
        val valid: Boolean = false,
    )

    private fun statistics(
        source: IntArray,
        target: IntArray,
        mask: ByteArray,
        stride: Int,
        x0: Int,
        y0: Int,
        x1: Int,
        y1: Int,
    ): Correction {
        var n = 0
        var sr = 0.0; var sg = 0.0; var sb = 0.0
        var tr = 0.0; var tg = 0.0; var tb = 0.0
        var sr2 = 0.0; var sg2 = 0.0; var sb2 = 0.0
        var tr2 = 0.0; var tg2 = 0.0; var tb2 = 0.0

        for (y in y0 until y1 step 2) {
            for (x in x0 until x1 step 2) {
                val i = y * stride + x
                if ((mask[i].toInt() and 0xff) < 64) continue
                val s = source[i]
                val t = target[i]
                val sR = ((s shr 16) and 0xff).toDouble()
                val sG = ((s shr 8) and 0xff).toDouble()
                val sB = (s and 0xff).toDouble()
                val tR = ((t shr 16) and 0xff).toDouble()
                val tG = ((t shr 8) and 0xff).toDouble()
                val tB = (t and 0xff).toDouble()
                sr += sR; sg += sG; sb += sB
                tr += tR; tg += tG; tb += tB
                sr2 += sR * sR; sg2 += sG * sG; sb2 += sB * sB
                tr2 += tR * tR; tg2 += tG * tG; tb2 += tB * tB
                n++
            }
        }
        if (n < 20) return Correction()

        fun channel(s: Double, s2: Double, t: Double, t2: Double): Pair<Float, Float> {
            val meanS = s / n
            val meanT = t / n
            val stdS = sqrt(max(1.0, s2 / n - meanS * meanS))
            val stdT = sqrt(max(1.0, t2 / n - meanT * meanT))
            // Keep identity texture/contrast from being crushed by extreme local noise.
            val gain = (stdT / stdS).coerceIn(0.82, 1.18)
            val offset = (meanT - meanS * gain).coerceIn(-38.0, 38.0)
            return gain.toFloat() to offset.toFloat()
        }

        val r = channel(sr, sr2, tr, tr2)
        val g = channel(sg, sg2, tg, tg2)
        val b = channel(sb, sb2, tb, tb2)
        return Correction(r.first, g.first, b.first, r.second, g.second, b.second, true)
    }

    private fun interpolate(
        c00: Correction,
        c10: Correction,
        c01: Correction,
        c11: Correction,
        fx: Float,
        fy: Float,
    ): Correction {
        val candidates = listOf(c00, c10, c01, c11)
        if (candidates.none { it.valid }) return Correction()
        fun lerp(a: Float, b: Float, t: Float) = a + (b - a) * t
        fun value(selector: (Correction) -> Float, fallback: Float): Float {
            val v00 = if (c00.valid) selector(c00) else fallback
            val v10 = if (c10.valid) selector(c10) else v00
            val v01 = if (c01.valid) selector(c01) else v00
            val v11 = if (c11.valid) selector(c11) else v10
            return lerp(lerp(v00, v10, fx), lerp(v01, v11, fx), fy)
        }
        return Correction(
            gainR = value({ it.gainR }, 1f),
            gainG = value({ it.gainG }, 1f),
            gainB = value({ it.gainB }, 1f),
            offsetR = value({ it.offsetR }, 0f),
            offsetG = value({ it.offsetG }, 0f),
            offsetB = value({ it.offsetB }, 0f),
            valid = true,
        )
    }
}
