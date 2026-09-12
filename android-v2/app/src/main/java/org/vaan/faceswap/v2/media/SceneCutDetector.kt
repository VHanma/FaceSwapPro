package org.vaan.faceswap.v2.media

import android.graphics.Bitmap
import kotlin.math.abs
import kotlin.math.max

/**
 * Lightweight hard-cut detector for temporal-state resets.
 * Samples the whole frame into a luma histogram plus coarse average RGB.
 * False positives are cheap: they only reset temporal smoothing for one frame.
 */
class SceneCutDetector {
    private var previousHistogram: FloatArray? = null
    private var previousRgb: FloatArray? = null

    fun isCut(bitmap: Bitmap): Boolean {
        val sample = sample(bitmap)
        val previousH = previousHistogram
        val previousC = previousRgb
        previousHistogram = sample.histogram
        previousRgb = sample.rgb
        if (previousH == null || previousC == null) return false

        var histogramDistance = 0f
        for (i in sample.histogram.indices) {
            histogramDistance += abs(sample.histogram[i] - previousH[i])
        }
        histogramDistance *= 0.5f // normalized L1 -> [0,1]

        val colorDistance = (
            abs(sample.rgb[0] - previousC[0]) +
                abs(sample.rgb[1] - previousC[1]) +
                abs(sample.rgb[2] - previousC[2])
            ) / (3f * 255f)

        return histogramDistance > 0.46f ||
            (histogramDistance > 0.30f && colorDistance > 0.20f) ||
            colorDistance > 0.42f
    }

    fun reset() {
        previousHistogram = null
        previousRgb = null
    }

    private data class Sample(val histogram: FloatArray, val rgb: FloatArray)

    private fun sample(bitmap: Bitmap): Sample {
        val bins = FloatArray(16)
        var rSum = 0L
        var gSum = 0L
        var bSum = 0L
        var count = 0

        // About 4k samples for a 1080p frame and proportionally similar for others.
        val stepX = max(1, bitmap.width / 64)
        val stepY = max(1, bitmap.height / 64)
        val row = IntArray(bitmap.width)
        var y = stepY / 2
        while (y < bitmap.height) {
            bitmap.getPixels(row, 0, bitmap.width, 0, y, bitmap.width, 1)
            var x = stepX / 2
            while (x < bitmap.width) {
                val p = row[x]
                val r = (p shr 16) and 0xff
                val g = (p shr 8) and 0xff
                val b = p and 0xff
                // Integer BT.709-ish luma, adequate for cut detection.
                val luma = (54 * r + 183 * g + 19 * b) shr 8
                bins[(luma ushr 4).coerceIn(0, 15)] += 1f
                rSum += r
                gSum += g
                bSum += b
                count++
                x += stepX
            }
            y += stepY
        }

        val safeCount = count.coerceAtLeast(1)
        for (i in bins.indices) bins[i] /= safeCount.toFloat()
        return Sample(
            histogram = bins,
            rgb = floatArrayOf(
                rSum.toFloat() / safeCount,
                gSum.toFloat() / safeCount,
                bSum.toFloat() / safeCount,
            ),
        )
    }
}
