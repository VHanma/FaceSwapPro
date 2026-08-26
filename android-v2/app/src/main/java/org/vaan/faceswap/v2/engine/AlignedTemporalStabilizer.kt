package org.vaan.faceswap.v2.engine

import android.graphics.Bitmap
import kotlin.math.abs
import kotlin.math.max

/**
 * Temporal consistency in normalized 512px face space.
 *
 * Because every target face has already been similarity-aligned, we can blend
 * neural identity detail and semantic alpha between neighboring frames without
 * ghosting the entire moving video frame. Weight automatically drops when pose,
 * blinking or mouth motion changes quickly so expressions stay responsive.
 */
class AlignedTemporalStabilizer : AutoCloseable {
    data class Output(val bitmap: Bitmap, val alpha: ByteArray)

    private var previousBitmap: Bitmap? = null
    private var previousAlpha: ByteArray? = null
    private var previousPose: FacePoseQuality.Pose? = null
    private var previousBlendshapes: Map<String, Float> = emptyMap()
    private var previousTimestampMs: Long = Long.MIN_VALUE

    fun stabilize(
        current: Bitmap,
        alpha: ByteArray,
        face: TrackerManager.TrackedFace,
        pose: FacePoseQuality.Pose,
    ): Output {
        require(current.width * current.height == alpha.size)
        val previous = previousBitmap
        val previousMask = previousAlpha
        val previousPoseValue = previousPose
        val dt = if (previousTimestampMs == Long.MIN_VALUE) Long.MAX_VALUE
        else face.timestampMs - previousTimestampMs

        val shouldReset = previous == null ||
            previousMask == null ||
            previous.width != current.width ||
            previous.height != current.height ||
            dt <= 0L ||
            dt > 140L ||
            previousPoseValue == null ||
            abs(previousPoseValue.yaw - pose.yaw) > 20f ||
            abs(previousPoseValue.pitch - pose.pitch) > 16f ||
            abs(previousPoseValue.roll - pose.roll) > 20f

        if (shouldReset) {
            reset()
            remember(current, alpha, face, pose)
            return Output(current.copy(Bitmap.Config.ARGB_8888, false), alpha.copyOf())
        }

        // Explicit non-null bindings keep the temporal state obvious to both the
        // compiler and future maintenance. Reaching this point means reset checks passed.
        val stablePrevious = previous ?: error("Temporal bitmap state vanished")
        val stableMask = previousMask ?: error("Temporal alpha state vanished")
        val stablePose = previousPoseValue ?: error("Temporal pose state vanished")

        val expressionDelta = max(
            expressionDelta(face.blendshapes, "jawOpen", "mouthFunnel", "mouthPucker"),
            expressionDelta(face.blendshapes, "eyeBlinkLeft", "eyeBlinkRight"),
        )
        val poseDelta = max(
            abs(stablePose.yaw - pose.yaw) / 20f,
            max(
                abs(stablePose.pitch - pose.pitch) / 16f,
                abs(stablePose.roll - pose.roll) / 20f,
            ),
        ).coerceIn(0f, 1f)

        // Calm frames get stronger history for pore/edge stability. Fast facial
        // movement gets less history so lips, eyes and brows remain responsive.
        val motion = max(expressionDelta * 1.6f, poseDelta).coerceIn(0f, 1f)
        val historyWeight = (0.34f * (1f - motion) + 0.08f * motion)
            .coerceIn(0.06f, 0.34f)

        val currentPixels = IntArray(alpha.size)
        val previousPixels = IntArray(alpha.size)
        current.getPixels(currentPixels, 0, current.width, 0, 0, current.width, current.height)
        stablePrevious.getPixels(
            previousPixels,
            0,
            stablePrevious.width,
            0,
            0,
            stablePrevious.width,
            stablePrevious.height,
        )
        val stabilizedAlpha = ByteArray(alpha.size)

        for (i in currentPixels.indices) {
            val currentA = alpha[i].toInt() and 0xff
            val previousA = stableMask[i].toInt() and 0xff
            // Only borrow history where both frames agree that this is identity
            // territory. This prevents stale face pixels bleeding over hair/mouth.
            val agreement = minOf(currentA, previousA) / 255f
            val w = historyWeight * agreement
            if (w > 0.001f) {
                val c = currentPixels[i]
                val p = previousPixels[i]
                val r = lerp((c shr 16) and 0xff, (p shr 16) and 0xff, w)
                val g = lerp((c shr 8) and 0xff, (p shr 8) and 0xff, w)
                val b = lerp(c and 0xff, p and 0xff, w)
                currentPixels[i] = (0xff shl 24) or (r shl 16) or (g shl 8) or b
            }
            stabilizedAlpha[i] = lerp(currentA, previousA, historyWeight * 0.45f)
                .coerceIn(0, 255)
                .toByte()
        }

        val output = Bitmap.createBitmap(
            currentPixels,
            current.width,
            current.height,
            Bitmap.Config.ARGB_8888,
        )
        remember(output, stabilizedAlpha, face, pose)
        return Output(output, stabilizedAlpha)
    }

    private fun expressionDelta(current: Map<String, Float>, vararg keys: String): Float {
        var delta = 0f
        for (key in keys) {
            delta = max(delta, abs((current[key] ?: 0f) - (previousBlendshapes[key] ?: 0f)))
        }
        return delta.coerceIn(0f, 1f)
    }

    private fun remember(
        bitmap: Bitmap,
        alpha: ByteArray,
        face: TrackerManager.TrackedFace,
        pose: FacePoseQuality.Pose,
    ) {
        previousBitmap?.recycle()
        previousBitmap = bitmap.copy(Bitmap.Config.ARGB_8888, false)
        previousAlpha = alpha.copyOf()
        previousPose = pose
        previousBlendshapes = face.blendshapes.toMap()
        previousTimestampMs = face.timestampMs
    }

    fun reset() {
        previousBitmap?.recycle()
        previousBitmap = null
        previousAlpha = null
        previousPose = null
        previousBlendshapes = emptyMap()
        previousTimestampMs = Long.MIN_VALUE
    }

    private fun lerp(current: Int, previous: Int, historyWeight: Float): Int =
        (current * (1f - historyWeight) + previous * historyWeight + 0.5f).toInt()

    override fun close() = reset()
}
