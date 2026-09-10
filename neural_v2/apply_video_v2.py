#!/usr/bin/env python3
"""Add native video face swapping to the FaceSwapPro Neural v2 build tree.

Run after apply_omega_v2.py. The video path keeps all inference on-device,
computes the source identity once, processes one target frame at a time through
the neural Ultra512 compositor, encodes H.264, copies the original audio track,
and saves the finished MP4 through Android MediaStore.
"""
from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("neural-upstream").resolve()
if not ROOT.exists():
    raise SystemExit(f"Upstream tree not found: {ROOT}")

JAVA = ROOT / "app/src/main/java/com/pv/androidfacefusion"


def replace_once(path: Path, old: str, new: str) -> None:
    s = path.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"Patch anchor missing in {path}: {old[:100]!r}")
    path.write_text(s.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# Version bump for the first video-capable Neural v2 alpha.
# ---------------------------------------------------------------------------
gradle = ROOT / "app/build.gradle.kts"
replace_once(gradle, 'versionCode = 20001', 'versionCode = 20002')
replace_once(gradle, 'versionName = "2.0.0-alpha1"', 'versionName = "2.0.1-alpha-video"')

# ---------------------------------------------------------------------------
# Make the image processor reusable for video: source identity is extracted
# exactly once; each frame only performs target detection + synthesis.
# ---------------------------------------------------------------------------
processor = JAVA / "FaceFusionProcessor.java"
anchor = '''    public FaceFusionProcessor(FaceDetector detector, FaceEmbedder embedder, FaceSwapper swapper) {
        this.faceDetector = detector;
        this.faceEmbedder = embedder;
        this.faceSwapper = swapper;
    }
'''
addition = anchor + r'''

    /** Extract one reusable source identity embedding for an entire video. */
    public float[] computeSourceEmbedding(Bitmap sourceImage) throws Exception {
        List<FaceDetector.Face> sourceFaces = faceDetector.detectFaces(sourceImage);
        if (sourceFaces.isEmpty()) {
            throw new Exception("No face detected in source image. Use a clear source portrait.");
        }
        Bitmap aligned = ImageUtils.alignFace(sourceImage, sourceFaces.get(0).landmarks, 112);
        try {
            return faceEmbedder.getEmbedding(aligned);
        } finally {
            if (!aligned.isRecycled()) aligned.recycle();
        }
    }

    /**
     * Swap the prepared identity into every face in one video frame.
     * A detector miss is treated as normal video behavior and the frame passes
     * through unchanged rather than aborting the render.
     */
    public Bitmap processVideoFrame(Bitmap targetImage, float[] sourceEmbedding) throws Exception {
        if (sourceEmbedding == null || sourceEmbedding.length == 0) {
            throw new IllegalArgumentException("Source embedding is empty");
        }
        List<FaceDetector.Face> targetFaces = faceDetector.detectFaces(targetImage);
        if (targetFaces.isEmpty()) {
            return targetImage.copy(Bitmap.Config.ARGB_8888, true);
        }

        Bitmap result = targetImage.copy(Bitmap.Config.ARGB_8888, true);
        for (FaceDetector.Face targetFace : targetFaces) {
            Bitmap aligned = null;
            Bitmap swapped = null;
            try {
                aligned = ImageUtils.alignFace(result, targetFace.landmarks, OmegaPixelBoost.ULTRA_SIZE);
                swapped = OmegaPixelBoost.swap(faceSwapper, aligned, sourceEmbedding, result);
                Bitmap blended = ImageUtils.blendFaces(
                    result, swapped, targetFace.landmarks, OmegaPixelBoost.ULTRA_SIZE);
                if (result != targetImage && !result.isRecycled()) result.recycle();
                result = blended;
            } finally {
                if (aligned != null && !aligned.isRecycled()) aligned.recycle();
                if (swapped != null && !swapped.isRecycled()) swapped.recycle();
            }
        }
        return result;
    }
'''
replace_once(processor, anchor, addition)

# ---------------------------------------------------------------------------
# Shared neural model hub. MainActivity publishes its already-loaded sessions,
# so VideoSwapActivity does not load a second ~740 MB model set into RAM.
# ---------------------------------------------------------------------------
(JAVA / "NeuralCore.java").write_text(r'''package com.pv.androidfacefusion;

import android.content.Context;

/** Process-wide holder for the large ONNX sessions. */
public final class NeuralCore {
    private static FaceDetector detector;
    private static FaceEmbedder embedder;
    private static FaceSwapper swapper;
    private static FaceFusionProcessor processor;

    private NeuralCore() {}

    public static synchronized void publish(FaceDetector d, FaceEmbedder e,
                                            FaceSwapper s, FaceFusionProcessor p) {
        detector = d;
        embedder = e;
        swapper = s;
        processor = p;
    }

    public static synchronized boolean isReady() {
        return processor != null;
    }

    public static synchronized FaceFusionProcessor getProcessor() {
        return processor;
    }

    /** Initialize once if video mode was opened directly after process restore. */
    public static synchronized FaceFusionProcessor initialize(Context context) throws Exception {
        if (processor != null) return processor;
        detector = new FaceDetector(context.getApplicationContext());
        detector.initialize();
        embedder = new FaceEmbedder(context.getApplicationContext());
        embedder.initialize();
        swapper = new FaceSwapper(context.getApplicationContext());
        swapper.initialize();
        processor = new FaceFusionProcessor(detector, embedder, swapper);
        return processor;
    }
}
''', encoding="utf-8")

# Publish the sessions MainActivity already loaded.
main = JAVA / "MainActivity.java"
replace_once(
    main,
    '                processor = new FaceFusionProcessor(faceDetector, faceEmbedder, faceSwapper);',
    '                processor = new FaceFusionProcessor(faceDetector, faceEmbedder, faceSwapper);\n'
    '                NeuralCore.publish(faceDetector, faceEmbedder, faceSwapper, processor);'
)

# Add a visible VIDEO SWAP button without disturbing the existing XML layout.
replace_once(
    main,
    '    private MaterialButton btnProcess, btnReset;',
    '    private MaterialButton btnProcess, btnReset;\n    private MaterialButton btnVideoSwap;'
)
replace_once(
    main,
    '        btnReset = findViewById(R.id.btnReset);',
    '''        btnReset = findViewById(R.id.btnReset);\n\n        btnVideoSwap = new MaterialButton(this);\n        btnVideoSwap.setText("VIDEO SWAP");\n        btnVideoSwap.setAllCaps(false);\n        LinearLayout.LayoutParams videoLp = new LinearLayout.LayoutParams(\n            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);\n        int videoMargin = (int) (12 * getResources().getDisplayMetrics().density);\n        videoLp.setMargins(videoMargin, videoMargin, videoMargin, videoMargin);\n        btnVideoSwap.setLayoutParams(videoLp);\n        standardSwapContainer.addView(btnVideoSwap);'''
)
replace_once(
    main,
    '        btnReset.setOnClickListener(v -> resetStandardSession());',
    '        btnReset.setOnClickListener(v -> resetStandardSession());\n'
    '        btnVideoSwap.setOnClickListener(v -> startActivity(new Intent(this, VideoSwapActivity.class)));'
)

# ---------------------------------------------------------------------------
# Native MP4 frame processor. It uses MediaMetadataRetriever as a conservative
# decoder path, H.264 MediaCodec for encoding, and MediaMuxer to preserve audio.
# ---------------------------------------------------------------------------
(JAVA / "NeuralVideoProcessor.java").write_text(r'''package com.pv.androidfacefusion;

import android.content.Context;
import android.graphics.Bitmap;
import android.media.MediaCodec;
import android.media.MediaCodecInfo;
import android.media.MediaExtractor;
import android.media.MediaFormat;
import android.media.MediaMetadataRetriever;
import android.media.MediaMuxer;
import android.net.Uri;
import android.util.Log;

import java.io.File;
import java.nio.ByteBuffer;

/** Memory-bounded offline neural video renderer. */
public final class NeuralVideoProcessor {
    private static final String TAG = "NeuralVideoProcessor";
    private static final String MIME_AVC = "video/avc";
    private static final long CODEC_TIMEOUT_US = 20_000;

    public interface Callback {
        void onStatus(String text);
        void onProgress(int percent, int frame, int totalFrames);
    }

    private NeuralVideoProcessor() {}

    public static void process(Context context, Bitmap sourceImage, Uri videoUri,
                               File outputFile, Callback callback) throws Exception {
        FaceFusionProcessor processor = NeuralCore.initialize(context);
        if (callback != null) callback.onStatus("Reading source identity...");
        float[] sourceEmbedding = processor.computeSourceEmbedding(sourceImage);

        MediaMetadataRetriever retriever = new MediaMetadataRetriever();
        MediaExtractor audioExtractor = new MediaExtractor();
        MediaCodec encoder = null;
        MediaMuxer muxer = null;

        try {
            retriever.setDataSource(context, videoUri);
            long durationMs = parseLong(retriever.extractMetadata(
                MediaMetadataRetriever.METADATA_KEY_DURATION), 0L);
            if (durationMs <= 0) throw new Exception("Could not read video duration");

            long metadataFrames = 0;
            try {
                metadataFrames = parseLong(retriever.extractMetadata(
                    MediaMetadataRetriever.METADATA_KEY_VIDEO_FRAME_COUNT), 0L);
            } catch (Throwable ignored) {}

            double fps = parseDouble(retriever.extractMetadata(
                MediaMetadataRetriever.METADATA_KEY_CAPTURE_FRAMERATE), 0.0);
            if (!(fps >= 1.0 && fps <= 120.0) && metadataFrames > 0) {
                fps = metadataFrames * 1000.0 / durationMs;
            }
            if (!(fps >= 1.0 && fps <= 120.0)) fps = 30.0;
            fps = Math.max(1.0, Math.min(60.0, fps));

            int totalFrames = (int) Math.max(1,
                Math.round((durationMs / 1000.0) * fps));

            Bitmap first = retriever.getFrameAtTime(0,
                MediaMetadataRetriever.OPTION_CLOSEST);
            if (first == null) throw new Exception("Could not decode first video frame");

            int width = first.getWidth() & ~1;
            int height = first.getHeight() & ~1;
            if (width < 2 || height < 2) throw new Exception("Invalid video dimensions");
            if (first.getWidth() != width || first.getHeight() != height) {
                Bitmap even = Bitmap.createScaledBitmap(first, width, height, true);
                if (!first.isRecycled()) first.recycle();
                first = even;
            }

            MediaCodecInfo.CodecCapabilities caps;
            encoder = MediaCodec.createEncoderByType(MIME_AVC);
            caps = encoder.getCodecInfo().getCapabilitiesForType(MIME_AVC);
            int colorFormat = chooseColorFormat(caps.colorFormats);

            MediaFormat format = MediaFormat.createVideoFormat(MIME_AVC, width, height);
            format.setInteger(MediaFormat.KEY_COLOR_FORMAT, colorFormat);
            int bitrate = (int) Math.max(8_000_000L,
                Math.min(40_000_000L, Math.round(width * (double) height * fps / 6.0)));
            format.setInteger(MediaFormat.KEY_BIT_RATE, bitrate);
            format.setInteger(MediaFormat.KEY_FRAME_RATE, (int) Math.max(1, Math.round(fps)));
            format.setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1);
            encoder.configure(format, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
            encoder.start();

            outputFile.getParentFile().mkdirs();
            if (outputFile.exists()) outputFile.delete();
            muxer = new MediaMuxer(outputFile.getAbsolutePath(),
                MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);

            audioExtractor.setDataSource(context, videoUri, null);
            int sourceAudioTrack = findTrack(audioExtractor, "audio/");
            int muxAudioTrack = -1;
            if (sourceAudioTrack >= 0) {
                audioExtractor.selectTrack(sourceAudioTrack);
                muxAudioTrack = muxer.addTrack(audioExtractor.getTrackFormat(sourceAudioTrack));
            }

            int muxVideoTrack = -1;
            boolean muxStarted = false;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            Bitmap lastDecoded = first;

            for (int frameIndex = 0; frameIndex < totalFrames; frameIndex++) {
                if (callback != null && frameIndex % Math.max(1, totalFrames / 200) == 0) {
                    int pct = (int) ((frameIndex * 100L) / totalFrames);
                    callback.onProgress(pct, frameIndex + 1, totalFrames);
                }

                long ptsUs = Math.round(frameIndex * 1_000_000.0 / fps);
                Bitmap decoded;
                if (frameIndex == 0) {
                    decoded = first;
                } else {
                    decoded = retriever.getFrameAtTime(ptsUs,
                        MediaMetadataRetriever.OPTION_CLOSEST);
                    if (decoded == null) {
                        decoded = lastDecoded.copy(Bitmap.Config.ARGB_8888, false);
                    }
                    if (decoded.getWidth() != width || decoded.getHeight() != height) {
                        Bitmap scaled = Bitmap.createScaledBitmap(decoded, width, height, true);
                        if (decoded != lastDecoded && !decoded.isRecycled()) decoded.recycle();
                        decoded = scaled;
                    }
                }

                if (lastDecoded != decoded && frameIndex > 1 && !lastDecoded.isRecycled()) {
                    lastDecoded.recycle();
                }
                lastDecoded = decoded;

                Bitmap swapped;
                try {
                    swapped = processor.processVideoFrame(decoded, sourceEmbedding);
                } catch (Exception frameError) {
                    Log.w(TAG, "Frame " + frameIndex + " swap failed; passing through", frameError);
                    swapped = decoded.copy(Bitmap.Config.ARGB_8888, true);
                }

                byte[] yuv = bitmapToYuv420(swapped, colorFormat);
                queueInput(encoder, yuv, ptsUs, false);
                if (swapped != decoded && !swapped.isRecycled()) swapped.recycle();

                DrainResult drained = drainEncoder(encoder, muxer, info, muxVideoTrack,
                    muxStarted, false);
                muxVideoTrack = drained.videoTrack;
                muxStarted = drained.muxStarted;
            }

            long eosPts = Math.round(totalFrames * 1_000_000.0 / fps);
            queueInput(encoder, new byte[0], eosPts, true);
            boolean eos = false;
            while (!eos) {
                DrainResult drained = drainEncoder(encoder, muxer, info, muxVideoTrack,
                    muxStarted, true);
                muxVideoTrack = drained.videoTrack;
                muxStarted = drained.muxStarted;
                eos = drained.eos;
            }

            if (lastDecoded != null && !lastDecoded.isRecycled()) lastDecoded.recycle();

            if (!muxStarted) throw new Exception("Video encoder produced no output format");
            if (sourceAudioTrack >= 0 && muxAudioTrack >= 0) {
                if (callback != null) callback.onStatus("Copying original audio...");
                copyAudio(audioExtractor, muxer, muxAudioTrack, durationMs * 1000L);
            }

            if (callback != null) callback.onProgress(100, totalFrames, totalFrames);
        } finally {
            try { retriever.release(); } catch (Throwable ignored) {}
            try { audioExtractor.release(); } catch (Throwable ignored) {}
            if (encoder != null) {
                try { encoder.stop(); } catch (Throwable ignored) {}
                try { encoder.release(); } catch (Throwable ignored) {}
            }
            if (muxer != null) {
                try { muxer.stop(); } catch (Throwable ignored) {}
                try { muxer.release(); } catch (Throwable ignored) {}
            }
        }
    }

    private static final class DrainResult {
        int videoTrack;
        boolean muxStarted;
        boolean eos;
        DrainResult(int videoTrack, boolean muxStarted, boolean eos) {
            this.videoTrack = videoTrack;
            this.muxStarted = muxStarted;
            this.eos = eos;
        }
    }

    private static DrainResult drainEncoder(MediaCodec encoder, MediaMuxer muxer,
                                             MediaCodec.BufferInfo info,
                                             int videoTrack, boolean muxStarted,
                                             boolean waitForEos) throws Exception {
        boolean eos = false;
        int emptyPolls = 0;
        while (true) {
            int outIndex = encoder.dequeueOutputBuffer(info, CODEC_TIMEOUT_US);
            if (outIndex == MediaCodec.INFO_TRY_AGAIN_LATER) {
                if (!waitForEos || ++emptyPolls > 250) break;
                continue;
            }
            if (outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                if (muxStarted) throw new Exception("Encoder output format changed twice");
                videoTrack = muxer.addTrack(encoder.getOutputFormat());
                muxer.start();
                muxStarted = true;
                continue;
            }
            if (outIndex >= 0) {
                ByteBuffer data = encoder.getOutputBuffer(outIndex);
                if (data == null) throw new Exception("Null encoder output buffer");
                if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                    info.size = 0;
                }
                if (info.size > 0) {
                    if (!muxStarted || videoTrack < 0) {
                        throw new Exception("Muxer not started before encoded frame");
                    }
                    data.position(info.offset);
                    data.limit(info.offset + info.size);
                    muxer.writeSampleData(videoTrack, data, info);
                }
                eos = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                encoder.releaseOutputBuffer(outIndex, false);
                if (eos) break;
            }
        }
        return new DrainResult(videoTrack, muxStarted, eos);
    }

    private static void queueInput(MediaCodec encoder, byte[] data, long ptsUs,
                                   boolean eos) throws Exception {
        int tries = 0;
        while (true) {
            int inputIndex = encoder.dequeueInputBuffer(CODEC_TIMEOUT_US);
            if (inputIndex >= 0) {
                ByteBuffer input = encoder.getInputBuffer(inputIndex);
                if (input == null) throw new Exception("Null encoder input buffer");
                input.clear();
                if (!eos) {
                    if (input.capacity() < data.length) {
                        throw new Exception("Encoder input buffer too small: " + input.capacity()
                            + " < " + data.length);
                    }
                    input.put(data);
                }
                encoder.queueInputBuffer(inputIndex, 0, eos ? 0 : data.length, ptsUs,
                    eos ? MediaCodec.BUFFER_FLAG_END_OF_STREAM : 0);
                return;
            }
            if (++tries > 250) throw new Exception("Timed out waiting for encoder input");
        }
    }

    private static int chooseColorFormat(int[] formats) throws Exception {
        for (int f : formats) if (f == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Planar) return f;
        for (int f : formats) if (f == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar) return f;
        for (int f : formats) if (f == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420Flexible) return f;
        throw new Exception("H.264 encoder exposes no YUV420 input format");
    }

    private static byte[] bitmapToYuv420(Bitmap bitmap, int colorFormat) {
        int w = bitmap.getWidth();
        int h = bitmap.getHeight();
        int[] argb = new int[w * h];
        bitmap.getPixels(argb, 0, w, 0, 0, w, h);
        byte[] out = new byte[w * h * 3 / 2];
        int ySize = w * h;
        int uvPlaneSize = ySize / 4;
        int uBase = ySize;
        int vBase = ySize + uvPlaneSize;
        boolean semi = colorFormat == MediaCodecInfo.CodecCapabilities.COLOR_FormatYUV420SemiPlanar;

        for (int y = 0; y < h; y++) {
            for (int x = 0; x < w; x++) {
                int c = argb[y * w + x];
                int r = (c >> 16) & 255;
                int g = (c >> 8) & 255;
                int b = c & 255;
                int yy = clamp(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16);
                out[y * w + x] = (byte) yy;
            }
        }

        for (int y = 0; y < h; y += 2) {
            for (int x = 0; x < w; x += 2) {
                int sumU = 0, sumV = 0, count = 0;
                for (int dy = 0; dy < 2 && y + dy < h; dy++) {
                    for (int dx = 0; dx < 2 && x + dx < w; dx++) {
                        int c = argb[(y + dy) * w + x + dx];
                        int r = (c >> 16) & 255;
                        int g = (c >> 8) & 255;
                        int b = c & 255;
                        sumU += clamp(((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128);
                        sumV += clamp(((112 * r - 94 * g - 18 * b + 128) >> 8) + 128);
                        count++;
                    }
                }
                int u = sumU / count;
                int v = sumV / count;
                int uvIndex = (y / 2) * (w / 2) + (x / 2);
                if (semi) {
                    int base = ySize + uvIndex * 2;
                    out[base] = (byte) u;
                    out[base + 1] = (byte) v;
                } else {
                    out[uBase + uvIndex] = (byte) u;
                    out[vBase + uvIndex] = (byte) v;
                }
            }
        }
        return out;
    }

    private static void copyAudio(MediaExtractor extractor, MediaMuxer muxer,
                                  int muxTrack, long maxPtsUs) {
        MediaFormat audioFormat = extractor.getTrackFormat(extractor.getSampleTrackIndex());
        int maxInput = audioFormat.containsKey(MediaFormat.KEY_MAX_INPUT_SIZE)
            ? audioFormat.getInteger(MediaFormat.KEY_MAX_INPUT_SIZE) : 1024 * 1024;
        maxInput = Math.max(maxInput, 256 * 1024);
        ByteBuffer buffer = ByteBuffer.allocateDirect(maxInput);
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        extractor.seekTo(0, MediaExtractor.SEEK_TO_CLOSEST_SYNC);
        while (true) {
            buffer.clear();
            int size = extractor.readSampleData(buffer, 0);
            if (size < 0) break;
            long pts = extractor.getSampleTime();
            if (pts < 0 || pts > maxPtsUs) break;
            info.set(0, size, pts, extractor.getSampleFlags());
            muxer.writeSampleData(muxTrack, buffer, info);
            if (!extractor.advance()) break;
        }
    }

    private static int findTrack(MediaExtractor extractor, String prefix) {
        for (int i = 0; i < extractor.getTrackCount(); i++) {
            MediaFormat f = extractor.getTrackFormat(i);
            String mime = f.getString(MediaFormat.KEY_MIME);
            if (mime != null && mime.startsWith(prefix)) return i;
        }
        return -1;
    }

    private static int clamp(int v) { return Math.max(0, Math.min(255, v)); }
    private static long parseLong(String s, long fallback) {
        try { return s == null ? fallback : Long.parseLong(s); } catch (Exception e) { return fallback; }
    }
    private static double parseDouble(String s, double fallback) {
        try { return s == null ? fallback : Double.parseDouble(s); } catch (Exception e) { return fallback; }
    }
}
''', encoding="utf-8")

# Fix audio format lookup safely by carrying the selected source track index.
video_processor = JAVA / "NeuralVideoProcessor.java"
vp = video_processor.read_text(encoding="utf-8")
vp = vp.replace(
    'copyAudio(audioExtractor, muxer, muxAudioTrack, durationMs * 1000L);',
    'copyAudio(audioExtractor, muxer, muxAudioTrack, sourceAudioTrack, durationMs * 1000L);')
vp = vp.replace(
    'private static void copyAudio(MediaExtractor extractor, MediaMuxer muxer,\n                                  int muxTrack, long maxPtsUs) {\n        MediaFormat audioFormat = extractor.getTrackFormat(extractor.getSampleTrackIndex());',
    'private static void copyAudio(MediaExtractor extractor, MediaMuxer muxer,\n                                  int muxTrack, int sourceTrack, long maxPtsUs) {\n        MediaFormat audioFormat = extractor.getTrackFormat(sourceTrack);')
video_processor.write_text(vp, encoding="utf-8")

# ---------------------------------------------------------------------------
# Programmatic video UI. OpenDocument gives a real video picker on Android 16.
# ---------------------------------------------------------------------------
(JAVA / "VideoSwapActivity.java").write_text(r'''package com.pv.androidfacefusion;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.database.Cursor;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Dedicated native neural video face-swap screen. */
public class VideoSwapActivity extends AppCompatActivity {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private Bitmap sourceBitmap;
    private Uri targetVideoUri;
    private ImageView sourcePreview;
    private TextView sourceLabel, videoLabel, status;
    private ProgressBar progress;
    private Button pickSource, pickVideo, render;

    private ActivityResultLauncher<String[]> sourcePicker;
    private ActivityResultLauncher<String[]> videoPicker;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle("FaceSwap Pro Neural Video");
        buildUi();
        setupPickers();
        warmModels();
    }

    private void buildUi() {
        int pad = dp(18);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText("Neural Video Swap • Ultra512");
        title.setTextSize(24);
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        root.addView(title, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView sub = new TextView(this);
        sub.setText("Choose one source face and a target video. The source identity is encoded once, then applied frame-by-frame. Original audio is preserved.");
        sub.setTextSize(15);
        LinearLayout.LayoutParams subLp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        subLp.setMargins(0, dp(10), 0, dp(14));
        root.addView(sub, subLp);

        sourcePreview = new ImageView(this);
        sourcePreview.setScaleType(ImageView.ScaleType.CENTER_CROP);
        LinearLayout.LayoutParams previewLp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(190));
        root.addView(sourcePreview, previewLp);

        pickSource = new Button(this);
        pickSource.setText("Choose source face");
        root.addView(pickSource, fullButtonLp());
        sourceLabel = new TextView(this);
        sourceLabel.setText("No source selected");
        root.addView(sourceLabel);

        pickVideo = new Button(this);
        pickVideo.setText("Choose target video");
        root.addView(pickVideo, fullButtonLp());
        videoLabel = new TextView(this);
        videoLabel.setText("No video selected");
        root.addView(videoLabel);

        render = new Button(this);
        render.setText("Render neural video");
        render.setEnabled(false);
        LinearLayout.LayoutParams renderLp = fullButtonLp();
        renderLp.setMargins(0, dp(18), 0, dp(8));
        root.addView(render, renderLp);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        root.addView(progress, new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, dp(12)));

        status = new TextView(this);
        status.setText("Loading neural engine...");
        status.setPadding(0, dp(10), 0, dp(20));
        root.addView(status);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(root);
        setContentView(scroll);
    }

    private LinearLayout.LayoutParams fullButtonLp() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, dp(10), 0, dp(5));
        return lp;
    }

    private void setupPickers() {
        sourcePicker = registerForActivityResult(new ActivityResultContracts.OpenDocument(), uri -> {
            if (uri == null) return;
            persistRead(uri);
            executor.execute(() -> {
                try {
                    Bitmap b = decodeBitmap(uri, 2048);
                    runOnUiThread(() -> {
                        if (sourceBitmap != null && !sourceBitmap.isRecycled()) sourceBitmap.recycle();
                        sourceBitmap = b;
                        sourcePreview.setImageBitmap(b);
                        sourceLabel.setText(displayName(uri));
                        updateRenderEnabled();
                    });
                } catch (Exception e) {
                    runOnUiThread(() -> Toast.makeText(this,
                        "Source image error: " + e.getMessage(), Toast.LENGTH_LONG).show());
                }
            });
        });

        videoPicker = registerForActivityResult(new ActivityResultContracts.OpenDocument(), uri -> {
            if (uri == null) return;
            persistRead(uri);
            targetVideoUri = uri;
            videoLabel.setText(displayName(uri));
            updateRenderEnabled();
        });

        pickSource.setOnClickListener(v -> sourcePicker.launch(new String[]{"image/*"}));
        pickVideo.setOnClickListener(v -> videoPicker.launch(new String[]{"video/*"}));
        render.setOnClickListener(v -> renderVideo());
    }

    private void warmModels() {
        executor.execute(() -> {
            try {
                if (!NeuralCore.isReady()) NeuralCore.initialize(this);
                runOnUiThread(() -> {
                    status.setText("Neural engine ready • video mode enabled");
                    updateRenderEnabled();
                });
            } catch (Exception e) {
                runOnUiThread(() -> status.setText("Neural engine error: " + e.getMessage()));
            }
        });
    }

    private void updateRenderEnabled() {
        render.setEnabled(sourceBitmap != null && targetVideoUri != null && NeuralCore.isReady());
    }

    private void renderVideo() {
        final Bitmap source = sourceBitmap;
        final Uri video = targetVideoUri;
        if (source == null || video == null) return;
        setControls(false);
        progress.setProgress(0);
        status.setText("Starting video render...");

        executor.execute(() -> {
            File temp = new File(getCacheDir(), "faceswappro_neural_" + System.currentTimeMillis() + ".mp4");
            try {
                NeuralVideoProcessor.process(this, source, video, temp,
                    new NeuralVideoProcessor.Callback() {
                        @Override public void onStatus(String text) {
                            runOnUiThread(() -> status.setText(text));
                        }
                        @Override public void onProgress(int percent, int frame, int totalFrames) {
                            runOnUiThread(() -> {
                                progress.setProgress(percent);
                                status.setText("Neural rendering " + frame + " / " + totalFrames + " frames • " + percent + "%");
                            });
                        }
                    });
                Uri saved = saveToMovies(temp);
                runOnUiThread(() -> {
                    progress.setProgress(100);
                    status.setText("Saved to Movies/FaceSwapPro\n" + saved);
                    Toast.makeText(this, "Neural video saved", Toast.LENGTH_LONG).show();
                    setControls(true);
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    status.setText("Video render failed: " + e.getMessage());
                    Toast.makeText(this, "Video render failed", Toast.LENGTH_LONG).show();
                    setControls(true);
                });
            } finally {
                if (temp.exists()) temp.delete();
            }
        });
    }

    private Uri saveToMovies(File temp) throws Exception {
        ContentValues values = new ContentValues();
        values.put(MediaStore.Video.Media.DISPLAY_NAME,
            "FaceSwapPro_Neural_" + System.currentTimeMillis() + ".mp4");
        values.put(MediaStore.Video.Media.MIME_TYPE, "video/mp4");
        values.put(MediaStore.Video.Media.RELATIVE_PATH, "Movies/FaceSwapPro");
        values.put(MediaStore.Video.Media.IS_PENDING, 1);
        ContentResolver resolver = getContentResolver();
        Uri outUri = resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values);
        if (outUri == null) throw new Exception("Could not create MediaStore video");
        try (InputStream in = new FileInputStream(temp);
             OutputStream out = resolver.openOutputStream(outUri, "w")) {
            if (out == null) throw new Exception("Could not open output video");
            byte[] buf = new byte[256 * 1024];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
        } catch (Exception e) {
            resolver.delete(outUri, null, null);
            throw e;
        }
        ContentValues done = new ContentValues();
        done.put(MediaStore.Video.Media.IS_PENDING, 0);
        resolver.update(outUri, done, null, null);
        return outUri;
    }

    private Bitmap decodeBitmap(Uri uri, int maxEdge) throws Exception {
        Bitmap b;
        try (InputStream in = getContentResolver().openInputStream(uri)) {
            b = BitmapFactory.decodeStream(in);
        }
        if (b == null) throw new Exception("Could not decode image");
        int max = Math.max(b.getWidth(), b.getHeight());
        if (max <= maxEdge) return b;
        float scale = maxEdge / (float) max;
        Bitmap scaled = Bitmap.createScaledBitmap(b,
            Math.max(1, Math.round(b.getWidth() * scale)),
            Math.max(1, Math.round(b.getHeight() * scale)), true);
        if (!b.isRecycled()) b.recycle();
        return scaled;
    }

    private String displayName(Uri uri) {
        try (Cursor c = getContentResolver().query(uri,
            new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
            if (c != null && c.moveToFirst()) return c.getString(0);
        } catch (Exception ignored) {}
        return uri.getLastPathSegment() == null ? "Selected media" : uri.getLastPathSegment();
    }

    private void persistRead(Uri uri) {
        try {
            getContentResolver().takePersistableUriPermission(uri,
                android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (Exception ignored) {}
    }

    private void setControls(boolean enabled) {
        pickSource.setEnabled(enabled);
        pickVideo.setEnabled(enabled);
        render.setEnabled(enabled && sourceBitmap != null && targetVideoUri != null && NeuralCore.isReady());
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }
}
''', encoding="utf-8")

# Register the new screen in the APK.
manifest = ROOT / "app/src/main/AndroidManifest.xml"
replace_once(
    manifest,
    '        <activity\n            android:name=".ImagePreviewActivity"',
    '        <activity\n            android:name=".VideoSwapActivity"\n            android:exported="false"\n            android:screenOrientation="portrait" />\n\n        <activity\n            android:name=".ImagePreviewActivity"'
)

# Static validation before Gradle gets a chance to run.
checks = {
    JAVA / "VideoSwapActivity.java": ["OpenDocument", "Render neural video", "Movies/FaceSwapPro"],
    JAVA / "NeuralVideoProcessor.java": ["MediaCodec", "MediaMuxer", "processVideoFrame", "copyAudio"],
    JAVA / "FaceFusionProcessor.java": ["computeSourceEmbedding", "processVideoFrame", "OmegaPixelBoost.ULTRA_SIZE"],
    JAVA / "MainActivity.java": ["VIDEO SWAP", "VideoSwapActivity.class", "NeuralCore.publish"],
    manifest: [".VideoSwapActivity"],
}
for path, needles in checks.items():
    text = path.read_text(encoding="utf-8")
    for needle in needles:
        if needle not in text:
            raise SystemExit(f"Video patch validation failed: {needle} missing from {path}")

print("OMEGA_VIDEO_V2_PATCH_OK")
print("video=OpenDocument -> frame neural Ultra512 -> H264 MediaCodec -> original audio -> MediaStore MP4")
