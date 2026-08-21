package org.vaan.faceswap.v2.engine

import android.content.Context
import android.media.MediaMetadataRetriever
import android.net.Uri
import kotlin.math.max
import kotlin.math.roundToInt

class VideoTrackingAnalyzer(private val context: Context) {

    data class Result(
        val sampledFrames: Int,
        val detectedFrames: Int,
        val averageLandmarks: Int,
        val minLandmarks: Int,
        val maxLandmarks: Int,
        val durationMs: Long,
        val declaredFrameCount: Int,
    ) {
        val detectionRate: Float
            get() = if (sampledFrames == 0) 0f else detectedFrames.toFloat() / sampledFrames

        val healthy: Boolean
            get() = sampledFrames > 0 && detectionRate >= 0.90f && averageLandmarks >= 470

        override fun toString(): String = buildString {
            append(if (healthy) "TRACKING PASS" else "TRACKING NEEDS WORK")
            append(" • detected ")
            append((detectionRate * 100f).roundToInt())
            append("% • landmarks avg=")
            append(averageLandmarks)
            append(" range=")
            append(minLandmarks)
            append("-")
            append(maxLandmarks)
            append(" • sampled=")
            append(sampledFrames)
        }
    }

    fun analyze(uri: Uri, maxSamples: Int = 30): Result {
        val retriever = MediaMetadataRetriever()
        retriever.setDataSource(context, uri)

        val durationMs = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
            ?.toLongOrNull() ?: 0L
        val declaredFrameCount = if (android.os.Build.VERSION.SDK_INT >= 28) {
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_FRAME_COUNT)
                ?.toIntOrNull() ?: 0
        } else 0

        val landmarkCounts = mutableListOf<Int>()
        var sampled = 0
        var detected = 0

        try {
            TrackerManager(context).use { tracker ->
                val sampleCount = when {
                    declaredFrameCount > 0 -> minOf(maxSamples, declaredFrameCount)
                    durationMs > 0 -> maxSamples
                    else -> 1
                }

                for (sampleIndex in 0 until sampleCount) {
                    val fraction = if (sampleCount <= 1) 0.0 else sampleIndex.toDouble() / (sampleCount - 1)
                    val bitmap = if (declaredFrameCount > 0 && android.os.Build.VERSION.SDK_INT >= 28) {
                        val frameIndex = (fraction * max(0, declaredFrameCount - 1)).roundToInt()
                        retriever.getFrameAtIndex(frameIndex)
                    } else {
                        val timeUs = (fraction * durationMs.toDouble() * 1000.0).toLong()
                        retriever.getFrameAtTime(timeUs, MediaMetadataRetriever.OPTION_CLOSEST)
                    } ?: continue

                    val timestampMs = (fraction * durationMs.toDouble()).toLong()
                    val faces = tracker.track(bitmap, timestampMs)
                    sampled += 1
                    if (faces.isNotEmpty()) {
                        detected += 1
                        landmarkCounts += faces.first().landmarks.size
                    }
                    bitmap.recycle()
                }
            }
        } finally {
            retriever.release()
        }

        val average = if (landmarkCounts.isEmpty()) 0 else landmarkCounts.average().roundToInt()
        return Result(
            sampledFrames = sampled,
            detectedFrames = detected,
            averageLandmarks = average,
            minLandmarks = landmarkCounts.minOrNull() ?: 0,
            maxLandmarks = landmarkCounts.maxOrNull() ?: 0,
            durationMs = durationMs,
            declaredFrameCount = declaredFrameCount,
        )
    }
}
