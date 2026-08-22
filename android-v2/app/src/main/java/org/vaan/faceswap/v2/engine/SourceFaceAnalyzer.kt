package org.vaan.faceswap.v2.engine

import android.content.Context
import android.graphics.Bitmap
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.facelandmarker.FaceLandmarker

/** IMAGE-mode dense face analysis for independent Identity Vault source photos. */
class SourceFaceAnalyzer(
    context: Context,
    modelAssetPath: String = "models/face_landmarker.task",
) : AutoCloseable {
    private val landmarker: FaceLandmarker

    init {
        val options = FaceLandmarker.FaceLandmarkerOptions.builder()
            .setBaseOptions(
                BaseOptions.builder()
                    .setModelAssetPath(modelAssetPath)
                    .build()
            )
            .setRunningMode(RunningMode.IMAGE)
            .setNumFaces(1)
            .setMinFaceDetectionConfidence(0.55f)
            .setMinFacePresenceConfidence(0.55f)
            .setOutputFaceBlendshapes(true)
            .setOutputFacialTransformationMatrixes(true)
            .build()
        landmarker = FaceLandmarker.createFromOptions(context, options)
    }

    fun analyze(bitmap: Bitmap): TrackerManager.TrackedFace? {
        val image = BitmapImageBuilder(bitmap).build()
        val result = landmarker.detect(image)
        val face = result.faceLandmarks().firstOrNull() ?: return null
        val blendshapes = result.faceBlendshapes().orElse(emptyList())
            .firstOrNull()
            ?.associate { category -> category.categoryName() to category.score() }
            ?: emptyMap()
        val transform = result.facialTransformationMatrixes().orElse(emptyList())
            .firstOrNull()
            ?.copyOf()
            ?: floatArrayOf()

        return TrackerManager.TrackedFace(
            landmarks = face.map { point -> TrackerManager.Point3(point.x(), point.y(), point.z()) },
            blendshapes = blendshapes,
            transformationMatrix = transform,
            timestampMs = 0L,
        )
    }

    override fun close() {
        landmarker.close()
    }
}
