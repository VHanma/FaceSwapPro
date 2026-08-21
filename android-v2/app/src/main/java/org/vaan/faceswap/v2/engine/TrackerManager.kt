package org.vaan.faceswap.v2.engine

import android.content.Context
import android.graphics.Bitmap
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.facelandmarker.FaceLandmarker

class TrackerManager(
    context: Context,
    modelAssetPath: String = "models/face_landmarker.task",
    maxFaces: Int = 1,
) : AutoCloseable {

    data class Point3(val x: Float, val y: Float, val z: Float)
    data class TrackedFace(val landmarks: List<Point3>)

    private val landmarker: FaceLandmarker

    init {
        val baseOptions = BaseOptions.builder()
            .setModelAssetPath(modelAssetPath)
            .build()
        val options = FaceLandmarker.FaceLandmarkerOptions.builder()
            .setBaseOptions(baseOptions)
            .setRunningMode(RunningMode.VIDEO)
            .setNumFaces(maxFaces)
            .setMinFaceDetectionConfidence(0.55f)
            .setMinFacePresenceConfidence(0.55f)
            .setMinTrackingConfidence(0.60f)
            .setOutputFaceBlendshapes(true)
            .setOutputFacialTransformationMatrixes(true)
            .build()
        landmarker = FaceLandmarker.createFromOptions(context, options)
    }

    fun track(bitmap: Bitmap, timestampMs: Long): List<TrackedFace> {
        val image = BitmapImageBuilder(bitmap).build()
        val result = landmarker.detectForVideo(image, timestampMs)
        return result.faceLandmarks().map { face ->
            TrackedFace(face.map { p -> Point3(p.x(), p.y(), p.z()) })
        }
    }

    override fun close() {
        landmarker.close()
    }
}
