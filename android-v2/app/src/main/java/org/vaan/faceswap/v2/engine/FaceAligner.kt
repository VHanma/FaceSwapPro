package org.vaan.faceswap.v2.engine

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import kotlin.math.max

/** Similarity alignment from MediaPipe dense landmarks to model-specific 5-point templates. */
object FaceAligner {
    data class P(val x: Float, val y: Float)

    enum class Template(val normalized: List<P>) {
        ARC_FACE_112_V1(
            listOf(
                P(0.35473214f, 0.45658929f),
                P(0.64526786f, 0.45658929f),
                P(0.50000000f, 0.61154464f),
                P(0.37913393f, 0.77687500f),
                P(0.62086607f, 0.77687500f),
            )
        ),
        ARC_FACE_112_V2(
            listOf(
                P(0.34191607f, 0.46157411f),
                P(0.65653393f, 0.45983393f),
                P(0.50022500f, 0.64050536f),
                P(0.37097589f, 0.82469196f),
                P(0.63151696f, 0.82325089f),
            )
        ),
        ARC_FACE_128(
            listOf(
                P(0.36167656f, 0.40387734f),
                P(0.63696719f, 0.40235469f),
                P(0.50019687f, 0.56044219f),
                P(0.38710391f, 0.72160547f),
                P(0.61507734f, 0.72034453f),
            )
        ),
        FFHQ_512(
            listOf(
                P(0.37691676f, 0.46864664f),
                P(0.62285697f, 0.46912813f),
                P(0.50123859f, 0.61331904f),
                P(0.39308822f, 0.72541100f),
                P(0.61150205f, 0.72490465f),
            )
        ),
    }

    data class Alignment(
        val bitmap: Bitmap,
        val sourceToAligned: Matrix,
        val alignedToSource: Matrix,
        val width: Int,
        val height: Int,
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

    fun align112(bitmap: Bitmap, points: List<P>): Bitmap =
        align(bitmap, points, Template.ARC_FACE_112_V2, 112, 112).bitmap

    fun align(
        bitmap: Bitmap,
        points: List<P>,
        template: Template,
        width: Int,
        height: Int = width,
    ): Alignment {
        require(points.size == 5)
        require(width > 0 && height > 0)
        val destination = template.normalized.map { P(it.x * width, it.y * height) }
        val forward = similarityTransform(points, destination)
        val inverse = Matrix()
        check(forward.invert(inverse)) { "Face alignment transform is singular" }

        val output = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        Canvas(output).drawBitmap(
            bitmap,
            forward,
            Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG),
        )
        return Alignment(output, forward, inverse, width, height)
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
