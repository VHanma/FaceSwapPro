package org.vaan.faceswap.v2.engine

import android.net.Uri
import org.vaan.faceswap.v2.model.ProcessingSettings

data class IdentityReference(
    val uri: Uri,
    val yaw: Float = 0f,
    val pitch: Float = 0f,
    val roll: Float = 0f,
    val sharpness: Float = 0f,
    val occlusionScore: Float = 0f,
    val identityScore: Float = 0f,
)

data class FrameQuality(
    val identity: Float,
    val geometry: Float,
    val mask: Float,
    val temporal: Float,
    val lighting: Float,
) {
    val overall: Float
        get() = identity * 0.30f + geometry * 0.22f + mask * 0.18f + temporal * 0.18f + lighting * 0.12f
}

interface IdentityVault {
    suspend fun build(references: List<Uri>): List<IdentityReference>
    fun bestReference(yaw: Float, pitch: Float, expressionHint: String? = null): IdentityReference?
}

interface SwapEngine {
    fun isAvailable(): Boolean
    fun modelName(): String
}

interface MaskEngine { fun isAvailable(): Boolean }
interface OcclusionEngine { fun isAvailable(): Boolean }
interface TemporalEngine { fun resetScene() }
interface RelightEngine { fun isAvailable(): Boolean }
interface RestorationEngine { fun isAvailable(): Boolean }

interface QualityControlEngine {
    fun shouldRerender(quality: FrameQuality, settings: ProcessingSettings): Boolean =
        settings.qualityMode.rerenderFailures && quality.overall < settings.minimumFrameQuality
}

data class PipelineStage(val name: String, val enabled: Boolean)

fun ProcessingSettings.stages(): List<PipelineStage> = listOf(
    PipelineStage("Decode / scene cuts", true),
    PipelineStage("478-point 3D tracking", true),
    PipelineStage("Pose-aware Identity Vault", true),
    PipelineStage("Neural identity swap", true),
    PipelineStage("Semantic face parsing", true),
    PipelineStage("Occlusion reconstruction", enableOcclusionRecovery),
    PipelineStage("Spatial relighting", enableRelighting),
    PipelineStage("Temporal stabilization", true),
    PipelineStage("Detail restoration", true),
    PipelineStage("Camera character match", enableCameraMatch),
    PipelineStage("Quality gate / rerender", qualityMode.rerenderFailures),
    PipelineStage("HQ master encode", true),
)
