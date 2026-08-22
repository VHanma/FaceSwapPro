package org.vaan.faceswap.v2.media

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaExtractor
import android.media.MediaFormat
import android.media.MediaMetadataRetriever
import android.media.MediaMuxer
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.vaan.faceswap.v2.engine.DefaultIdentityVault
import org.vaan.faceswap.v2.engine.MovieFaceSwapEngine
import org.vaan.faceswap.v2.engine.NeuralModelPackManager
import org.vaan.faceswap.v2.engine.TrackerManager
import org.vaan.faceswap.v2.model.ProcessingSettings
import org.vaan.faceswap.v2.model.QualityMode
import java.io.File
import java.nio.ByteBuffer
import kotlin.math.max

/**
 * Full v2 CPU-readable video path:
 * MediaExtractor -> MediaCodec YUV decode -> neural replacement -> AVC encode -> MediaMuxer.
 * Original compressed audio samples are copied without generational re-encoding.
 */
class MovieVideoProcessor(private val context: Context) {

    data class Result(
        val savedUri: Uri,
        val processedFrames: Int,
        val swappedFrames: Int,
        val durationUs: Long,
        val sceneCuts: Int = 0,
    )

    private data class TrackInfo(
        val index: Int,
        val format: MediaFormat,
    )

    private data class MuxState(
        var videoTrack: Int = -1,
        var audioTrack: Int = -1,
        var started: Boolean = false,
    )

    suspend fun process(
        identitySources: List<Uri>,
        targetVideo: Uri,
        outputName: String = "FaceSwapPro-v2-${System.currentTimeMillis()}.mp4",
        settings: ProcessingSettings = ProcessingSettings(QualityMode.MOVIE),
        progress: (message: String, percent: Int) -> Unit = { _, _ -> },
        cancelled: () -> Boolean = { false },
    ): Result = withContext(Dispatchers.Default) {
        require(identitySources.isNotEmpty()) { "Choose at least one Identity Vault image" }
        val pack = NeuralModelPackManager(context)
        require(pack.verifyInstalled()) { "Install and verify the Movie Neural Pack first" }

        progress("Building pose-aware Identity Vault", 1)
        val vault = DefaultIdentityVault(context)
        vault.build(identitySources)

        val extractor = MediaExtractor()
        extractor.setDataSource(context, targetVideo, null)
        val videoTrack = findTrack(extractor, "video/")
            ?: run {
                extractor.release()
                error("Target file has no video track")
            }
        val audioTrack = findTrack(extractor, "audio/")
        extractor.selectTrack(videoTrack.index)

        val sourceWidth = videoTrack.format.getInteger(MediaFormat.KEY_WIDTH)
        val sourceHeight = videoTrack.format.getInteger(MediaFormat.KEY_HEIGHT)
        val outputWidth = sourceWidth and 1.inv()
        val outputHeight = sourceHeight and 1.inv()
        require(outputWidth > 0 && outputHeight > 0)
        val fps = readFrameRate(videoTrack.format).coerceIn(1, 120)
        val durationUs = readDurationUs(videoTrack.format, targetVideo).coerceAtLeast(1L)
        val rotation = readRotation(targetVideo)

        val tempOutput = File(context.cacheDir, "faceswap_v2_${System.nanoTime()}.mp4")
        if (tempOutput.exists()) tempOutput.delete()

        val muxer = MediaMuxer(tempOutput.absolutePath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        if (rotation != 0) muxer.setOrientationHint(rotation)
        val muxState = MuxState()
        if (audioTrack != null) muxState.audioTrack = muxer.addTrack(audioTrack.format)

        val decoderFormat = videoTrack.format
        decoderFormat.setInteger(
            MediaFormat.KEY_COLOR_FORMAT,
            MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible,
        )
        val videoMime = decoderFormat.getString(MediaFormat.KEY_MIME)
            ?: error("Video MIME type is missing")
        val decoder = MediaCodec.createDecoderByType(videoMime)
        decoder.configure(decoderFormat, null, null, 0)

        val encoderInfo = chooseAvcEncoder()
        val encoderCapabilities = encoderInfo.getCapabilitiesForType(MediaFormat.MIMETYPE_VIDEO_AVC)
        val encoderColor = Yuv420Converter.chooseEncoderColorFormat(encoderCapabilities.colorFormats)
        val encoderFormat = MediaFormat.createVideoFormat(
            MediaFormat.MIMETYPE_VIDEO_AVC,
            outputWidth,
            outputHeight,
        ).apply {
            setInteger(MediaFormat.KEY_COLOR_FORMAT, encoderColor)
            setInteger(MediaFormat.KEY_FRAME_RATE, fps)
            setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
            setInteger(MediaFormat.KEY_BIT_RATE, chooseBitrate(outputWidth, outputHeight, fps, settings.qualityMode))
            if (encoderCapabilities.profileLevels.any { it.profile == MediaCodecInfo.CodecProfileLevel.AVCProfileHigh }) {
                setInteger(MediaFormat.KEY_PROFILE, MediaCodecInfo.CodecProfileLevel.AVCProfileHigh)
            }
        }
        val encoder = MediaCodec.createByCodecName(encoderInfo.name)
        encoder.configure(encoderFormat, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)

        var processedFrames = 0
        var swappedFrames = 0
        var sceneCuts = 0
        var decoderInputDone = false
        var decoderOutputDone = false
        var encoderOutputDone = false
        val decoderInfo = MediaCodec.BufferInfo()
        val encoderInfoBuffer = MediaCodec.BufferInfo()
        val cutDetector = SceneCutDetector()

        try {
            decoder.start()
            encoder.start()
            TrackerManager(context).use { tracker ->
                MovieFaceSwapEngine(context, vault, pack).use { swapEngine ->
                    while (!decoderOutputDone) {
                        if (cancelled()) error("Cancelled")

                        if (!decoderInputDone) {
                            val inputIndex = decoder.dequeueInputBuffer(TIMEOUT_US)
                            if (inputIndex >= 0) {
                                val inputBuffer = decoder.getInputBuffer(inputIndex)
                                    ?: error("Decoder input buffer unavailable")
                                inputBuffer.clear()
                                val sampleSize = extractor.readSampleData(inputBuffer, 0)
                                if (sampleSize < 0) {
                                    decoder.queueInputBuffer(
                                        inputIndex,
                                        0,
                                        0,
                                        0,
                                        MediaCodec.BUFFER_FLAG_END_OF_STREAM,
                                    )
                                    decoderInputDone = true
                                } else {
                                    decoder.queueInputBuffer(
                                        inputIndex,
                                        0,
                                        sampleSize,
                                        extractor.sampleTime,
                                        extractor.sampleFlags,
                                    )
                                    extractor.advance()
                                }
                            }
                        }

                        val outputIndex = decoder.dequeueOutputBuffer(decoderInfo, TIMEOUT_US)
                        when {
                            outputIndex >= 0 -> {
                                val endOfStream = decoderInfo.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                                if (decoderInfo.size > 0) {
                                    val image = decoder.getOutputImage(outputIndex)
                                        ?: error("Decoder did not expose a YUV image for frame $processedFrames")
                                    val decoded = try {
                                        Yuv420Converter.imageToBitmap(image)
                                    } finally {
                                        image.close()
                                    }

                                    val upright = rotate(decoded, rotation)
                                    if (upright !== decoded) decoded.recycle()

                                    // A hard edit must never inherit the previous shot's neural
                                    // history. False positives are harmless: they only disable
                                    // smoothing for a frame.
                                    if (cutDetector.isCut(upright)) {
                                        sceneCuts++
                                        swapEngine.resetTemporal()
                                    }

                                    val timestampMs = max(0L, decoderInfo.presentationTimeUs / 1000L)
                                    val faces = tracker.track(upright, timestampMs)

                                    val processedUpright = if (faces.isNotEmpty()) {
                                        val swapped = swapEngine.swapFrame(upright, faces.first())
                                        swappedFrames++
                                        swapped.bitmap
                                    } else {
                                        // Tracking loss also breaks temporal continuity. When the
                                        // face returns it starts clean instead of borrowing stale pixels.
                                        swapEngine.resetTemporal()
                                        upright.copy(Bitmap.Config.ARGB_8888, false)
                                    }

                                    val encodedOrientation = rotate(processedUpright, inverseRotation(rotation))
                                    if (encodedOrientation !== processedUpright) processedUpright.recycle()
                                    val encoderFrame = if (
                                        encodedOrientation.width != outputWidth ||
                                        encodedOrientation.height != outputHeight
                                    ) {
                                        Bitmap.createScaledBitmap(
                                            encodedOrientation,
                                            outputWidth,
                                            outputHeight,
                                            true,
                                        ).also { encodedOrientation.recycle() }
                                    } else encodedOrientation

                                    queueEncoderFrame(
                                        encoder,
                                        encoderFrame,
                                        encoderColor,
                                        decoderInfo.presentationTimeUs,
                                        muxer,
                                        muxState,
                                        encoderInfoBuffer,
                                    )
                                    encoderFrame.recycle()
                                    upright.recycle()
                                    processedFrames++

                                    val percent = ((decoderInfo.presentationTimeUs * 94L) / durationUs)
                                        .toInt().coerceIn(1, 94)
                                    progress(
                                        "Movie neural frame $processedFrames • swapped $swappedFrames • cuts $sceneCuts",
                                        percent,
                                    )
                                }
                                decoder.releaseOutputBuffer(outputIndex, false)
                                if (endOfStream) decoderOutputDone = true
                            }
                            outputIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> Unit
                        }

                        drainEncoder(
                            encoder,
                            muxer,
                            muxState,
                            encoderInfoBuffer,
                            endOfStream = false,
                        )
                    }
                }
            }

            signalEncoderEndOfStream(encoder, muxer, muxState, encoderInfoBuffer)
            while (!encoderOutputDone) {
                encoderOutputDone = drainEncoder(
                    encoder,
                    muxer,
                    muxState,
                    encoderInfoBuffer,
                    endOfStream = true,
                )
            }

            require(muxState.started && muxState.videoTrack >= 0) { "Encoder produced no video track" }
            progress("Copying original audio", 96)
            if (audioTrack != null && muxState.audioTrack >= 0) {
                copyAudio(targetVideo, audioTrack.index, muxer, muxState.audioTrack)
            }
        } catch (throwable: Throwable) {
            tempOutput.delete()
            throw throwable
        } finally {
            extractor.release()
            runCatching { decoder.stop() }
            decoder.release()
            runCatching { encoder.stop() }
            encoder.release()
            if (muxState.started) runCatching { muxer.stop() }
            muxer.release()
        }

        require(tempOutput.isFile && tempOutput.length() > 1024L) { "Video mastering produced no usable MP4" }
        progress("Publishing HQ master", 99)
        val savedUri = try {
            publishVideo(tempOutput, outputName)
        } finally {
            tempOutput.delete()
        }
        progress("FaceSwap Pro v2 Movie master complete", 100)
        Result(savedUri, processedFrames, swappedFrames, durationUs, sceneCuts)
    }

    private fun queueEncoderFrame(
        encoder: MediaCodec,
        frame: Bitmap,
        colorFormat: Int,
        presentationTimeUs: Long,
        muxer: MediaMuxer,
        muxState: MuxState,
        info: MediaCodec.BufferInfo,
    ) {
        while (true) {
            val inputIndex = encoder.dequeueInputBuffer(TIMEOUT_US)
            if (inputIndex >= 0) {
                val buffer = encoder.getInputBuffer(inputIndex)
                    ?: error("Encoder input buffer unavailable")
                val size = Yuv420Converter.bitmapToEncoderBuffer(frame, buffer, colorFormat)
                encoder.queueInputBuffer(inputIndex, 0, size, presentationTimeUs, 0)
                return
            }
            drainEncoder(encoder, muxer, muxState, info, endOfStream = false)
        }
    }

    private fun signalEncoderEndOfStream(
        encoder: MediaCodec,
        muxer: MediaMuxer,
        muxState: MuxState,
        info: MediaCodec.BufferInfo,
    ) {
        while (true) {
            val inputIndex = encoder.dequeueInputBuffer(TIMEOUT_US)
            if (inputIndex >= 0) {
                encoder.queueInputBuffer(
                    inputIndex,
                    0,
                    0,
                    0,
                    MediaCodec.BUFFER_FLAG_END_OF_STREAM,
                )
                return
            }
            drainEncoder(encoder, muxer, muxState, info, endOfStream = false)
        }
    }

    /** Returns true after the encoder EOS output buffer is observed. */
    private fun drainEncoder(
        encoder: MediaCodec,
        muxer: MediaMuxer,
        state: MuxState,
        info: MediaCodec.BufferInfo,
        endOfStream: Boolean,
    ): Boolean {
        while (true) {
            val index = encoder.dequeueOutputBuffer(info, if (endOfStream) TIMEOUT_US else 0L)
            when {
                index == MediaCodec.INFO_TRY_AGAIN_LATER -> return false
                index == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    check(state.videoTrack < 0) { "Encoder output format changed twice" }
                    state.videoTrack = muxer.addTrack(encoder.outputFormat)
                    muxer.start()
                    state.started = true
                }
                index >= 0 -> {
                    val output = encoder.getOutputBuffer(index)
                        ?: error("Encoder output buffer unavailable")
                    if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) info.size = 0
                    if (info.size > 0) {
                        check(state.started) { "Muxer has not started" }
                        output.position(info.offset)
                        output.limit(info.offset + info.size)
                        muxer.writeSampleData(state.videoTrack, output, info)
                    }
                    val eos = info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0
                    encoder.releaseOutputBuffer(index, false)
                    if (eos) return true
                }
            }
        }
    }

    private fun copyAudio(source: Uri, sourceTrackIndex: Int, muxer: MediaMuxer, muxTrack: Int) {
        val extractor = MediaExtractor()
        try {
            extractor.setDataSource(context, source, null)
            extractor.selectTrack(sourceTrackIndex)
            val format = extractor.getTrackFormat(sourceTrackIndex)
            val maxInput = if (format.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)) {
                format.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE).coerceAtLeast(256 * 1024)
            } else 1024 * 1024
            val buffer = ByteBuffer.allocateDirect(maxInput)
            val info = MediaCodec.BufferInfo()
            while (true) {
                buffer.clear()
                val size = extractor.readSampleData(buffer, 0)
                if (size < 0) break
                info.set(0, size, extractor.sampleTime, extractor.sampleFlags)
                buffer.position(0)
                buffer.limit(size)
                muxer.writeSampleData(muxTrack, buffer, info)
                extractor.advance()
            }
        } finally {
            extractor.release()
        }
    }

    private fun findTrack(extractor: MediaExtractor, prefix: String): TrackInfo? {
        for (index in 0 until extractor.trackCount) {
            val format = extractor.getTrackFormat(index)
            val mime = format.getString(MediaFormat.KEY_MIME) ?: continue
            if (mime.startsWith(prefix)) return TrackInfo(index, format)
        }
        return null
    }

    private fun chooseAvcEncoder(): MediaCodecInfo {
        val infos = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
        return infos.firstOrNull { info ->
            info.isEncoder && info.supportedTypes.any { it.equals(MediaFormat.MIMETYPE_VIDEO_AVC, true) }
        } ?: error("This device has no H.264 encoder")
    }

    private fun readFrameRate(format: MediaFormat): Int =
        if (format.containsKey(MediaFormat.KEY_FRAME_RATE)) format.getInteger(MediaFormat.KEY_FRAME_RATE) else 30

    private fun readDurationUs(format: MediaFormat, uri: Uri): Long {
        if (format.containsKey(MediaFormat.KEY_DURATION)) return format.getLong(MediaFormat.KEY_DURATION)
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, uri)
            (retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull() ?: 0L) * 1000L
        } finally {
            retriever.release()
        }
    }

    private fun readRotation(uri: Uri): Int {
        val retriever = MediaMetadataRetriever()
        return try {
            retriever.setDataSource(context, uri)
            val raw = retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)
                ?.toIntOrNull() ?: 0
            ((raw % 360) + 360) % 360
        } finally {
            retriever.release()
        }
    }

    private fun rotate(bitmap: Bitmap, degrees: Int): Bitmap {
        val normalized = ((degrees % 360) + 360) % 360
        if (normalized == 0) return bitmap
        val matrix = Matrix().apply { postRotate(normalized.toFloat()) }
        return Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, matrix, true)
    }

    private fun inverseRotation(degrees: Int): Int = (360 - (((degrees % 360) + 360) % 360)) % 360

    private fun chooseBitrate(width: Int, height: Int, fps: Int, mode: QualityMode): Int {
        val pixelsPerSecond = width.toLong() * height.toLong() * fps.toLong()
        val multiplier = when (mode) {
            QualityMode.FAST -> 0.12
            QualityMode.BALANCED -> 0.18
            QualityMode.MOVIE -> 0.28
        }
        return (pixelsPerSecond * multiplier).toLong()
            .coerceIn(6_000_000L, 80_000_000L)
            .toInt()
    }

    private fun publishVideo(file: File, requestedName: String): Uri {
        val cleanName = requestedName
            .replace(Regex("[^A-Za-z0-9._-]"), "_")
            .let { if (it.endsWith(".mp4", true)) it else "$it.mp4" }
        val values = ContentValues().apply {
            put(MediaStore.Video.Media.DISPLAY_NAME, cleanName)
            put(MediaStore.Video.Media.MIME_TYPE, "video/mp4")
            put(MediaStore.Video.Media.RELATIVE_PATH, Environment.DIRECTORY_MOVIES + "/FaceSwapPro")
            put(MediaStore.Video.Media.IS_PENDING, 1)
        }
        val resolver = context.contentResolver
        val uri = resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values)
            ?: error("Could not create output video in MediaStore")
        try {
            resolver.openOutputStream(uri)?.use { output ->
                file.inputStream().use { input -> input.copyTo(output, 1024 * 1024) }
            } ?: error("Could not open output video destination")
            values.clear()
            values.put(MediaStore.Video.Media.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return uri
        } catch (throwable: Throwable) {
            resolver.delete(uri, null, null)
            throw throwable
        }
    }

    companion object {
        private const val TIMEOUT_US = 10_000L
    }
}
