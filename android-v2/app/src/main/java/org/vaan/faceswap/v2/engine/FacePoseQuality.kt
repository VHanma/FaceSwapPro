package org.vaan.faceswap.v2.engine

import android.graphics.Bitmap
import kotlin.math.PI
import kotlin.math.atan2
import kotlin.math.hypot

object FacePoseQuality {
    data class Pose(val yaw: Float, val pitch: Float, val roll: Float)

    fun estimatePose(face: TrackerManager.TrackedFace): Pose {
        val p = face.landmarks
        fun point(index: Int) = p[index]
        fun average(vararg indices: Int): TrackerManager.Point3 {
            var x = 0f
            var y = 0f
            var z = 0f
            for (index in indices) {
                x += p[index].x
                y += p[index].y
                z += p[index].z
            }
            val n = indices.size.toFloat()
            return TrackerManager.Point3(x / n, y / n, z / n)
        }

        val leftEye = average(33, 133, 159, 145)
        val rightEye = average(362, 263, 386, 374)
        val eyeMidX = (leftEye.x + rightEye.x) * 0.5f
        val eyeMidY = (leftEye.y + rightEye.y) * 0.5f
        val eyeDistance = hypot(rightEye.x - leftEye.x, rightEye.y - leftEye.y).coerceAtLeast(1e-4f)
        val nose = point(1)
        val mouthLeft = point(61)
        val mouthRight = point(291)
        val mouthMidY = (mouthLeft.y + mouthRight.y) * 0.5f

        // Robust lightweight pose cues from dense geometry. The neural transform
        // matrix remains available for a future calibrated 3D pose backend.
        val yaw = (((nose.x - eyeMidX) / eyeDistance) * 75f).coerceIn(-70f, 70f)
        val eyeToMouth = (mouthMidY - eyeMidY).coerceAtLeast(1e-4f)
        val noseRatio = (nose.y - eyeMidY) / eyeToMouth
        val pitch = ((noseRatio - 0.52f) * 95f).coerceIn(-50f, 50f)
        val roll = (atan2(rightEye.y - leftEye.y, rightEye.x - leftEye.x) * 180f / PI).toFloat()

        return Pose(yaw, pitch, roll)
    }

    /** Normalized local-gradient energy. Used to avoid blurry Identity Vault references. */
    fun sharpness(bitmap: Bitmap): Float {
        val sampleW = minOf(bitmap.width, 256)
        val sampleH = minOf(bitmap.height, 256)
        val sampled = if (bitmap.width == sampleW && bitmap.height == sampleH) {
            bitmap
        } else Bitmap.createScaledBitmap(bitmap, sampleW, sampleH, true)

        val pixels = IntArray(sampleW * sampleH)
        sampled.getPixels(pixels, 0, sampleW, 0, 0, sampleW, sampleH)
        var energy = 0.0
        var count = 0
        fun lum(pixel: Int): Int {
            val r = (pixel shr 16) and 0xff
            val g = (pixel shr 8) and 0xff
            val b = pixel and 0xff
            return (r * 77 + g * 150 + b * 29) shr 8
        }
        for (y in 1 until sampleH - 1) {
            for (x in 1 until sampleW - 1) {
                val i = y * sampleW + x
                val gx = lum(pixels[i + 1]) - lum(pixels[i - 1])
                val gy = lum(pixels[i + sampleW]) - lum(pixels[i - sampleW])
                energy += gx * gx + gy * gy
                count++
            }
        }
        if (sampled !== bitmap) sampled.recycle()
        val mean = if (count == 0) 0.0 else energy / count
        return (mean / 1800.0).toFloat().coerceIn(0f, 1f)
    }
}
