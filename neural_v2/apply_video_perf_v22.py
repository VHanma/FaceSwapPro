#!/usr/bin/env python3
"""Performance rescue for Neural v2 video on Android phones.

Runs after apply_video_v2.py. Photos keep the Ultra512 compositor. Video uses a
single native INSwapper pass per frame, limits render cadence to 12 fps, caps
the working frame to 1280 px on the long edge, and swaps only the dominant face.
That removes the 16-pass-per-frame bottleneck that made v2.0.1 appear frozen.
"""
from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("neural-upstream").resolve()
JAVA = ROOT / "app/src/main/java/com/pv/androidfacefusion"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Performance patch anchor missing in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Version bump.
gradle = ROOT / "app/build.gradle.kts"
replace_once(gradle, 'versionCode = 20002', 'versionCode = 20003')
replace_once(gradle, 'versionName = "2.0.1-alpha-video"', 'versionName = "2.0.2-alpha-video-fast"')

# Replace the video-frame path only. Still images continue to call Ultra512.
processor = JAVA / "FaceFusionProcessor.java"
old_method = r'''    public Bitmap processVideoFrame(Bitmap targetImage, float[] sourceEmbedding) throws Exception {
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
new_method = r'''    public Bitmap processVideoFrame(Bitmap targetImage, float[] sourceEmbedding) throws Exception {
        if (sourceEmbedding == null || sourceEmbedding.length == 0) {
            throw new IllegalArgumentException("Source embedding is empty");
        }

        // Video is intentionally a different performance profile from photos.
        // One INSwapper pass at native 128 avoids sixteen sequential neural
        // passes per frame. The detector still follows pose/expression each frame.
        List<FaceDetector.Face> targetFaces = faceDetector.detectFaces(targetImage);
        if (targetFaces.isEmpty()) {
            return targetImage.copy(Bitmap.Config.ARGB_8888, true);
        }

        // Dominant-face mode keeps mobile video responsive. Pick the largest
        // visible face rather than multiplying inference by every background face.
        FaceDetector.Face targetFace = targetFaces.get(0);
        float bestArea = Math.max(0f, targetFace.bbox.width()) * Math.max(0f, targetFace.bbox.height());
        for (int i = 1; i < targetFaces.size(); i++) {
            FaceDetector.Face candidate = targetFaces.get(i);
            float area = Math.max(0f, candidate.bbox.width()) * Math.max(0f, candidate.bbox.height());
            if (area > bestArea) {
                targetFace = candidate;
                bestArea = area;
            }
        }

        Bitmap aligned = null;
        Bitmap swapped = null;
        try {
            aligned = ImageUtils.alignFace(targetImage, targetFace.landmarks, 128);
            swapped = faceSwapper.swapFace(aligned, sourceEmbedding, targetImage);
            if (swapped == null) {
                return targetImage.copy(Bitmap.Config.ARGB_8888, true);
            }
            return ImageUtils.blendFaces(targetImage, swapped, targetFace.landmarks, 128);
        } finally {
            if (aligned != null && !aligned.isRecycled()) aligned.recycle();
            if (swapped != null && !swapped.isRecycled()) swapped.recycle();
        }
    }
'''
replace_once(processor, old_method, new_method)

# Reduce decoder seeks + whole-frame CPU conversion load.
video = JAVA / "NeuralVideoProcessor.java"
replace_once(
    video,
    '    private static final long CODEC_TIMEOUT_US = 20_000;\n',
    '    private static final long CODEC_TIMEOUT_US = 20_000;\n'
    '    private static final double MAX_RENDER_FPS = 12.0;\n'
    '    private static final int MAX_LONG_EDGE = 1280;\n'
)
replace_once(
    video,
    '            fps = Math.max(1.0, Math.min(60.0, fps));',
    '            fps = Math.max(1.0, Math.min(MAX_RENDER_FPS, fps));'
)
old_dims = '''            int width = first.getWidth() & ~1;
            int height = first.getHeight() & ~1;
            if (width < 2 || height < 2) throw new Exception("Invalid video dimensions");
            if (first.getWidth() != width || first.getHeight() != height) {
                Bitmap even = Bitmap.createScaledBitmap(first, width, height, true);
                if (!first.isRecycled()) first.recycle();
                first = even;
            }
'''
new_dims = '''            int srcWidth = first.getWidth();
            int srcHeight = first.getHeight();
            int srcLong = Math.max(srcWidth, srcHeight);
            double frameScale = srcLong > MAX_LONG_EDGE ? (MAX_LONG_EDGE / (double) srcLong) : 1.0;
            int width = Math.max(2, ((int) Math.round(srcWidth * frameScale)) & ~1);
            int height = Math.max(2, ((int) Math.round(srcHeight * frameScale)) & ~1);
            if (width < 2 || height < 2) throw new Exception("Invalid video dimensions");
            if (first.getWidth() != width || first.getHeight() != height) {
                Bitmap even = Bitmap.createScaledBitmap(first, width, height, true);
                if (!first.isRecycled()) first.recycle();
                first = even;
            }
            if (callback != null) {
                callback.onStatus("Fast neural video • " + ((int) Math.round(fps)) + " fps • " + width + "x" + height);
            }
'''
replace_once(video, old_dims, new_dims)
replace_once(
    video,
    '            int bitrate = (int) Math.max(8_000_000L,\n                Math.min(40_000_000L, Math.round(width * (double) height * fps / 6.0)));',
    '            int bitrate = (int) Math.max(4_000_000L,\n                Math.min(18_000_000L, Math.round(width * (double) height * fps / 4.5)));'
)

# Make the UI accurately describe the new mobile profile.
activity = JAVA / "VideoSwapActivity.java"
replace_once(activity, 'title.setText("Neural Video Swap • Ultra512");', 'title.setText("Neural Video Swap • FAST");')
replace_once(
    activity,
    'sub.setText("Choose one source face and a target video. The source identity is encoded once, then applied frame-by-frame. Original audio is preserved.");',
    'sub.setText("Fast mobile neural video: one neural pass per frame, dominant-face tracking, up to 12 fps and 1280px working resolution. Photos still use Ultra512. Original audio is preserved.");'
)
replace_once(activity, 'render.setText("Render neural video");', 'render.setText("Render FAST neural video");')
replace_once(activity, 'status.setText("Neural engine ready • video mode enabled");', 'status.setText("Neural engine ready • FAST video mode");')

# Static guards. Any reintroduction of Ultra512 in processVideoFrame should fail CI.
proc_text = processor.read_text(encoding="utf-8")
method_start = proc_text.index('public Bitmap processVideoFrame')
method_end = proc_text.index('\n    }', method_start) + len('\n    }')
video_method = proc_text[method_start:method_end]
if 'OmegaPixelBoost.swap' in video_method or 'ULTRA_SIZE' in video_method:
    raise SystemExit('Ultra512 still present in mobile video method')
if 'faceSwapper.swapFace' not in video_method or 'targetFace.bbox.width()' not in video_method:
    raise SystemExit('Fast dominant-face neural path missing')

vtext = video.read_text(encoding="utf-8")
for needle in ['MAX_RENDER_FPS = 12.0', 'MAX_LONG_EDGE = 1280', 'Fast neural video']:
    if needle not in vtext:
        raise SystemExit(f'Missing video performance guard: {needle}')

print('OMEGA_VIDEO_PERF_V22_OK')
print('video=single neural pass/frame; dominant face; max 12fps; max 1280px; photos=Ultra512')
