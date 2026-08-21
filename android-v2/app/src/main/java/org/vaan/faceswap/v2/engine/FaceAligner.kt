package org.vaan.faceswap.v2.engine

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import kotlin.math.max

/** Similarity alignment from MediaPipe dense landmarks to the standard 112px face template. */
object FaceAligner {
    data class P(val x: Float, val y: Float)

    private val destination = listOf(
        P(38.2946f, 51.6963f),
        P(73.5318f, 51.5014f),
        P(56.0252f, 71.7366f),
        P(41.5493f, 92.3655f),
        P(70.7299f, 92.2041f),
    )

    fun fivePoints(
        landmarks: List<TrackerManager.Point3>,
        width: Int,
        height: Int,
    ): List<P> {
        require(landmarks.size > 386) { "Dense MediaPipe landmarks required" }

        fun point(index: Int) = P(
            landmarks[index].x * width,
            landmarks[index].y * height,
        )
        fun average(vararg indices: Int): P {
            var x = 0f
            var y = 0f
            for (index in indices) {
                val p = point(index)
                x += p.x
                y += p.y
            }
            return P(x / indices.size, y / indices.size)
        }

        return listOf(
            average(33, 133, 159, 145),
            average(362, 263, 386, 374),
            point(1),
            point(61),
            point(291),
        )
    }

    fun align112(bitmap: Bitmap, points: List<P>): Bitmap {
        require(points.size == 5)
        val matrix = similarityTransform(points, destination)
        val output = Bitmap.createBitmap(112, 112, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(output)
        canvas.drawBitmap(
            bitmap,
            matrix,
            Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG),
        )
        return output
    }

    /**
     * Least-squares 2D similarity transform:
     * x' = a*x - b*y + tx
     * y' = b*x + a*y + ty
     */
    private fun similarityTransform(source: List<P>, target: List<P>): Matrix {
        val n = source.size.toFloat()
        val sx = source.sumOf { it.x.toDouble() }.toFloat() / n
        val sy = source.sumOf { it.y.toDouble() }.toFloat() / n
        val txMean = target.sumOf { it.x.toDouble() }.toFloat() / n
        val tyMean = target.sumOf { it.y.toDouble() }.toFloat() / n

        var denom = 0.0
        var aNumerator = 0.0
        var bNumerator = 0.0
        for (i in source.indices) {
            val x = (source[i].x - sx).toDouble()
            val y = (source[i].y - sy).toDouble()
            val u = (target[i].x - txMean).toDouble()
            val v = (target[i].y - tyMean).toDouble()
            denom += x * x + y * y
            aNumerator += x * u + y * v
            bNumerator += x * v - y * u
        }
        val safeDenom = max(denom, 1e-9)
        val a = (aNumerator / safeDenom).toFloat()
        val b = (bNumerator / safeDenom).toFloat()
        val translateX = txMean - a * sx + b * sy
        val translateY = tyMean - b * sx - a * sy

        return Matrix().apply {
            setValues(
                floatArrayOf(
                    a, -b, translateX,
                    b, a, translateY,
                    0f, 0f, 1f,
                )
            )
        }
    }
}
