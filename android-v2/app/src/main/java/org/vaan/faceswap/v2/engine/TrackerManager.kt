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

    data class TrackedFace(
        val landmarks: List<Point3>,
        val blendshapes: Map<String, Float> = emptyMap(),
        /** 4x4 canonical-face -> detected-face matrix, flat column-major. */
        val transformationMatrix: FloatArray = floatArrayOf(),
        val timestampMs: Long = 0L,
    ) {
        fun expression(name: String): Float = blendshapes[name] ?: 0f

        val mouthOpen: Float
            get() = maxOf(expression("jawOpen"), expression("mouthFunnel"), expression("mouthPucker"))

        val blink: Float
            get() = maxOf(expression("eyeBlinkLeft"), expression("eyeBlinkRight"))
    }

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
        val blendshapeFaces = result.faceBlendshapes().orElse(emptyList())
        val transforms = result.facialTransformationMatrixes().orElse(emptyList())

        return result.faceLandmarks().mapIndexed { index, face ->
            val blendshapes = blendshapeFaces.getOrNull(index)
                ?.associate { category -> category.categoryName() to category.score() }
                ?: emptyMap()
            TrackedFace(
                landmarks = face.map { p -> Point3(p.x(), p.y(), p.z()) },
                blendshapes = blendshapes,
                transformationMatrix = transforms.getOrNull(index)?.copyOf() ?: floatArrayOf(),
                timestampMs = result.timestampMs(),
            )
        }
    }

    override fun close() {
        landmarker.close()
    }
}
