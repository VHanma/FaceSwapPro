package org.vaan.faceswap.v2.engine

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.media.MediaMetadataRetriever
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class NeuralSwapPreviewRunner(private val context: Context) {

    data class Result(
        val savedUri: Uri,
        val sourceYaw: Float,
        val targetYaw: Float,
        val targetPitch: Float,
    )

    suspend fun run(
        identitySources: List<Uri>,
        targetVideo: Uri,
        pack: NeuralModelPackManager,
    ): Result = withContext(Dispatchers.Default) {
        require(pack.verifyInstalled()) { "Movie Neural Pack is not installed or failed verification" }
        val vault = DefaultIdentityVault(context)
        vault.build(identitySources)

        val targetFrame = extractPreviewFrame(targetVideo)
        try {
            val targetFace = SourceFaceAnalyzer(context).use { analyzer ->
                analyzer.analyze(targetFrame)
                    ?: error("No face detected in the preview frame")
            }
            MovieFaceSwapEngine(context, vault, pack).use { engine ->
                val swapped = engine.swapFrame(targetFrame, targetFace)
                try {
                    val uri = savePng(swapped.bitmap)
                    Result(
                        savedUri = uri,
                        sourceYaw = swapped.sourceReference.yaw,
                        targetYaw = swapped.targetPose.yaw,
                        targetPitch = swapped.targetPose.pitch,
                    )
                } finally {
                    swapped.bitmap.recycle()
                }
            }
        } finally {
            targetFrame.recycle()
        }
    }

    private fun extractPreviewFrame(video: Uri): Bitmap {
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, video)
            val durationMs = retriever
                .extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull()
                ?: 0L
            val timeUs = (durationMs.coerceAtLeast(0L) * 1000L) / 2L
            retriever.getFrameAtTime(timeUs, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
                ?: retriever.getFrameAtTime(timeUs, MediaMetadataRetriever.OPTION_CLOSEST)
                ?: error("Could not decode a target preview frame")
        } finally {
            retriever.release()
        }
    }

    private fun savePng(bitmap: Bitmap): Uri {
        val resolver = context.contentResolver
        val name = "FaceSwapPro-v2-preview-${System.currentTimeMillis()}.png"
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, name)
            put(MediaStore.Images.Media.MIME_TYPE, "image/png")
            put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/FaceSwapPro")
            put(MediaStore.Images.Media.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
            ?: error("Could not create preview in MediaStore")
        try {
            resolver.openOutputStream(uri)?.use { output ->
                check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)) {
                    "PNG preview encoding failed"
                }
            } ?: error("Could not open preview output")
            values.clear()
            values.put(MediaStore.Images.Media.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return uri
        } catch (throwable: Throwable) {
            resolver.delete(uri, null, null)
            throw throwable
        }
    }
}
