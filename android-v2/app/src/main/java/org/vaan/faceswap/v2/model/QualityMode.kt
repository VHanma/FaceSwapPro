package org.vaan.faceswap.v2.model

enum class QualityMode(
    val label: String,
    val internalFaceSize: Int,
    val temporalRadius: Int,
    val refinementPasses: Int,
    val rerenderFailures: Boolean,
) {
    FAST("Fast", 256, 1, 0, false),
    BALANCED("Balanced", 512, 2, 1, false),
    MOVIE("Movie", 512, 2, 2, true),
}

data class ProcessingSettings(
    val qualityMode: QualityMode,
    val preserveTargetMouthInterior: Boolean = true,
    val preserveEyeHighlights: Boolean = true,
    val enableOcclusionRecovery: Boolean = qualityMode != QualityMode.FAST,
    val enableRelighting: Boolean = qualityMode != QualityMode.FAST,
    val enableCameraMatch: Boolean = qualityMode == QualityMode.MOVIE,
    // Calibrated for the independent composite score, where 1.0 means all five
    // identity/geometry/mask/temporal/lighting gates are essentially ideal.
    val minimumFrameQuality: Float = if (qualityMode == QualityMode.MOVIE) 0.80f else 0.70f,
)
