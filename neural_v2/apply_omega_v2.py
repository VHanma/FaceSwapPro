#!/usr/bin/env python3
"""Clean-room FaceSwapPro neural-v2 patch layer for the pinned MIT Android chassis.

The build workflow clones a pinned upstream android-face-fusion commit, then this
script applies FaceSwapPro branding, ARM64 packaging, high-resolution phase pixel
boost, and correctly scaled alignment landmarks. The neural engine remains fully
on-device after its model pack is downloaded by the app.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("neural-upstream").resolve()
if not ROOT.exists():
    raise SystemExit(f"Upstream tree not found: {ROOT}")


def replace_required(path: Path, old: str, new: str, count: int | None = None) -> None:
    text = path.read_text(encoding="utf-8")
    found = text.count(old)
    if found == 0:
        raise SystemExit(f"Required patch anchor missing in {path}: {old[:80]!r}")
    if count is not None and found != count:
        raise SystemExit(f"Unexpected anchor count in {path}: expected {count}, found {found}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# ---------------------------------------------------------------------------
# App identity + ARM64-only distribution. Keep upstream Java namespace intact
# to minimize risk, but give the install a FaceSwapPro-specific application ID.
# This allows side-by-side A/B testing against the Python v1.5 build.
# ---------------------------------------------------------------------------
gradle = ROOT / "app/build.gradle.kts"
replace_required(gradle, 'applicationId = "com.pv.androidfacefusion"', 'applicationId = "org.vaan.faceswap.neuralv2"', 1)
replace_required(gradle, 'versionCode = 1', 'versionCode = 20001', 1)
replace_required(gradle, 'versionName = "1.0"', 'versionName = "2.0.0-alpha1"', 1)
replace_required(
    gradle,
    'abiFilters.addAll(listOf("armeabi-v7a", "arm64-v8a", "x86", "x86_64"))',
    'abiFilters.add("arm64-v8a")',
    1,
)

strings = ROOT / "app/src/main/res/values/strings.xml"
replace_required(strings, '<string name="app_name">AndroidFaceFusion</string>', '<string name="app_name">FaceSwap Pro Neural v2</string>', 1)

# ---------------------------------------------------------------------------
# Scale the standard INSwapper 128 template for 256/512 boosted alignment.
# The upstream code treated every non-112 target as a 128 coordinate space,
# which would place a 512 crop into the wrong geometry.
# ---------------------------------------------------------------------------
image_utils = ROOT / "app/src/main/java/com/pv/androidfacefusion/ImageUtils.java"
text = image_utils.read_text(encoding="utf-8")
anchor = '''    private static final float[][] FFHQ_SRC_128 = {
        {38.2946f + 8.0f, 51.6963f},  // left eye
        {73.5318f + 8.0f, 51.5014f},  // right eye
        {56.0252f + 8.0f, 71.7366f},  // nose
        {41.5493f + 8.0f, 92.3655f},  // left mouth
        {70.7299f + 8.0f, 92.2041f}   // right mouth
    };
'''
helper = anchor + '''
    /** Returns the ArcFace/INSwapper template scaled into the requested crop. */
    private static float[][] referenceLandmarks(int targetSize) {
        if (targetSize == 112) {
            return ARCFACE_SRC;
        }
        if (targetSize == 128) {
            return FFHQ_SRC_128;
        }
        float scale = targetSize / 128.0f;
        float[][] scaled = new float[FFHQ_SRC_128.length][2];
        for (int i = 0; i < FFHQ_SRC_128.length; i++) {
            scaled[i][0] = FFHQ_SRC_128[i][0] * scale;
            scaled[i][1] = FFHQ_SRC_128[i][1] * scale;
        }
        return scaled;
    }
'''
if anchor not in text:
    raise SystemExit("FFHQ reference landmark anchor missing")
text = text.replace(anchor, helper, 1)
text = text.replace('float[][] refLandmarks = (targetSize == 112) ? ARCFACE_SRC : FFHQ_SRC_128;', 'float[][] refLandmarks = referenceLandmarks(targetSize);')
text = text.replace('float[][] refLandmarks = (faceSize == 112) ? ARCFACE_SRC : FFHQ_SRC_128;', 'float[][] refLandmarks = referenceLandmarks(faceSize);')
if text.count('referenceLandmarks(') < 3:
    raise SystemExit("Reference-landmark scaling patch did not apply to both align and blend paths")
image_utils.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Public FaceFusion pixel-boost concept ported to Android.
# Important: these are phase/interleaved full-face samples, not spatial tiles.
# 512 => 4x4 phases => sixteen native 128x128 neural passes.
# ---------------------------------------------------------------------------
pixel_boost = ROOT / "app/src/main/java/com/pv/androidfacefusion/OmegaPixelBoost.java"
pixel_boost.write_text(r'''package com.pv.androidfacefusion;

import android.graphics.Bitmap;
import android.util.Log;

/**
 * Omega phase pixel boost.
 *
 * A 512x512 aligned face is de-interleaved into sixteen 128x128 full-face
 * phase samples. Each sample is synthesized by the native neural swapper and
 * the results are interleaved back into one 512x512 face. This mirrors the
 * public pixel-boost idea used by modern desktop face-swap pipelines while
 * keeping the model itself at its native resolution.
 */
public final class OmegaPixelBoost {
    private static final String TAG = "OmegaPixelBoost";
    public static final int MODEL_SIZE = 128;
    public static final int ULTRA_SIZE = 512;
    private static final int BALANCED_SIZE = 256;

    private OmegaPixelBoost() {}

    /** Always returns a 512x512 face so the paste-back transform stays stable. */
    public static Bitmap swap(FaceSwapper swapper, Bitmap alignedFace, float[] sourceEmbedding, Bitmap targetImage) {
        try {
            Log.i(TAG, "Omega ULTRA: 512 phase boost (16 neural passes)");
            return phaseBoost(swapper, alignedFace, sourceEmbedding, targetImage, 4);
        } catch (OutOfMemoryError oom) {
            Log.w(TAG, "512 boost hit memory limit; falling back to 256 phase boost", oom);
            System.gc();
        } catch (RuntimeException ex) {
            Log.w(TAG, "512 boost failed; falling back to 256 phase boost", ex);
        }

        try {
            Bitmap balanced = Bitmap.createScaledBitmap(alignedFace, BALANCED_SIZE, BALANCED_SIZE, true);
            Bitmap swapped = phaseBoost(swapper, balanced, sourceEmbedding, targetImage, 2);
            Bitmap ultra = Bitmap.createScaledBitmap(swapped, ULTRA_SIZE, ULTRA_SIZE, true);
            if (balanced != alignedFace && !balanced.isRecycled()) balanced.recycle();
            if (swapped != ultra && !swapped.isRecycled()) swapped.recycle();
            return ultra;
        } catch (OutOfMemoryError oom) {
            Log.w(TAG, "256 boost hit memory limit; falling back to native 128", oom);
            System.gc();
        } catch (RuntimeException ex) {
            Log.w(TAG, "256 boost failed; falling back to native 128", ex);
        }

        Bitmap fast = Bitmap.createScaledBitmap(alignedFace, MODEL_SIZE, MODEL_SIZE, true);
        Bitmap swapped = swapper.swapFace(fast, sourceEmbedding, targetImage);
        if (swapped == null) throw new IllegalStateException("Native neural swap returned null");
        Bitmap ultra = Bitmap.createScaledBitmap(swapped, ULTRA_SIZE, ULTRA_SIZE, true);
        if (fast != alignedFace && fast != swapped && !fast.isRecycled()) fast.recycle();
        if (swapped != ultra && !swapped.isRecycled()) swapped.recycle();
        return ultra;
    }

    private static Bitmap phaseBoost(FaceSwapper swapper, Bitmap input, float[] sourceEmbedding,
                                     Bitmap targetImage, int phases) {
        final int boostedSize = MODEL_SIZE * phases;
        Bitmap aligned = input;
        if (input.getWidth() != boostedSize || input.getHeight() != boostedSize) {
            aligned = Bitmap.createScaledBitmap(input, boostedSize, boostedSize, true);
        }

        int[] source = new int[boostedSize * boostedSize];
        int[] output = new int[boostedSize * boostedSize];
        int[] phasePixels = new int[MODEL_SIZE * MODEL_SIZE];
        int[] swappedPixels = new int[MODEL_SIZE * MODEL_SIZE];
        aligned.getPixels(source, 0, boostedSize, 0, 0, boostedSize, boostedSize);

        for (int py = 0; py < phases; py++) {
            for (int px = 0; px < phases; px++) {
                for (int y = 0; y < MODEL_SIZE; y++) {
                    int srcRow = (y * phases + py) * boostedSize;
                    int dstRow = y * MODEL_SIZE;
                    for (int x = 0; x < MODEL_SIZE; x++) {
                        phasePixels[dstRow + x] = source[srcRow + x * phases + px];
                    }
                }

                Bitmap phase = Bitmap.createBitmap(phasePixels, MODEL_SIZE, MODEL_SIZE, Bitmap.Config.ARGB_8888);
                Bitmap swapped = swapper.swapFace(phase, sourceEmbedding, targetImage);
                if (swapped == null) {
                    if (!phase.isRecycled()) phase.recycle();
                    throw new IllegalStateException("Neural phase swap returned null at " + px + "," + py);
                }
                if (swapped.getWidth() != MODEL_SIZE || swapped.getHeight() != MODEL_SIZE) {
                    Bitmap resized = Bitmap.createScaledBitmap(swapped, MODEL_SIZE, MODEL_SIZE, true);
                    if (resized != swapped && !swapped.isRecycled()) swapped.recycle();
                    swapped = resized;
                }
                swapped.getPixels(swappedPixels, 0, MODEL_SIZE, 0, 0, MODEL_SIZE, MODEL_SIZE);

                for (int y = 0; y < MODEL_SIZE; y++) {
                    int outRow = (y * phases + py) * boostedSize;
                    int srcRow = y * MODEL_SIZE;
                    for (int x = 0; x < MODEL_SIZE; x++) {
                        output[outRow + x * phases + px] = swappedPixels[srcRow + x];
                    }
                }

                if (!phase.isRecycled()) phase.recycle();
                if (!swapped.isRecycled()) swapped.recycle();
            }
        }

        if (aligned != input && !aligned.isRecycled()) aligned.recycle();
        return Bitmap.createBitmap(output, boostedSize, boostedSize, Bitmap.Config.ARGB_8888);
    }
}
''', encoding="utf-8")

# ---------------------------------------------------------------------------
# Route every target-face neural render through 512 phase boost. Source identity
# extraction remains 112x112 ArcFace, exactly as required by the reference core.
# ---------------------------------------------------------------------------
processor = ROOT / "app/src/main/java/com/pv/androidfacefusion/FaceFusionProcessor.java"
p = processor.read_text(encoding="utf-8")
patches = {
    'ImageUtils.alignFace(targetImage, targetFace.landmarks, 128)':
        'ImageUtils.alignFace(targetImage, targetFace.landmarks, OmegaPixelBoost.ULTRA_SIZE)',
    'ImageUtils.alignFace(result, targetFace.landmarks, 128)':
        'ImageUtils.alignFace(result, targetFace.landmarks, OmegaPixelBoost.ULTRA_SIZE)',
    'faceSwapper.swapFace(alignedTargetFace, sourceEmbedding, targetImage)':
        'OmegaPixelBoost.swap(faceSwapper, alignedTargetFace, sourceEmbedding, targetImage)',
    'faceSwapper.swapFace(alignedTargetFace, sourceEmbedding, result)':
        'OmegaPixelBoost.swap(faceSwapper, alignedTargetFace, sourceEmbedding, result)',
    'ImageUtils.blendFaces(targetImage, swappedFace, targetFace.landmarks, 128)':
        'ImageUtils.blendFaces(targetImage, swappedFace, targetFace.landmarks, OmegaPixelBoost.ULTRA_SIZE)',
    'ImageUtils.blendFaces(result, swappedFace, targetFace.landmarks, 128)':
        'ImageUtils.blendFaces(result, swappedFace, targetFace.landmarks, OmegaPixelBoost.ULTRA_SIZE)',
}
for old, new in patches.items():
    if old in p:
        p = p.replace(old, new)

if 'OmegaPixelBoost.swap(' not in p or 'OmegaPixelBoost.ULTRA_SIZE' not in p:
    raise SystemExit("FaceFusionProcessor Omega routing patch did not apply")
if 'alignFace(targetImage, targetFace.landmarks, 128)' in p or 'alignFace(result, targetFace.landmarks, 128)' in p:
    raise SystemExit("A target INSwapper 128 alignment path remains unpatched")
processor.write_text(p, encoding="utf-8")

# License/attribution stored inside the APK assets. Models retain their own terms.
assets = ROOT / "app/src/main/assets"
assets.mkdir(parents=True, exist_ok=True)
(assets / "THIRD_PARTY_NOTICES.txt").write_text(
    "FaceSwap Pro Neural v2 uses a clean-room quality layer on top of the MIT-licensed\n"
    "android-face-fusion reference implementation by Parasaran-Python.\n"
    "Source: https://github.com/Parasaran-Python/android-face-fusion\n\n"
    "Face swap model files are downloaded separately at runtime and remain subject\n"
    "to their upstream model licenses.\n",
    encoding="utf-8",
)

print("OMEGA_V2_PATCH_OK")
print(f"patched={ROOT}")
print("render=512x512 phase-interleaved pixel boost; fallback=256/128")
