# FaceSwap Pro Working Clone

An offline Android face-swap app rebuilt from the FaceSwapPro concept.

## What was repaired

- Added the missing module-level `process_video()` entry point.
- Added Android-buildable NumPy and OpenCV requirements.
- Bundled the Haar face detector instead of assuming desktop OpenCV data files exist.
- Replaced obsolete direct external-storage writes with Android MediaStore.
- Uses Android's document picker, so broad storage access is unnecessary.
- Added cancellation, accurate progress updates, codec checks, and clearer errors.
- Removed the fake server/upload status. Processing stays on-device.

## Use

1. Choose a clear source face photo.
2. Choose a target MP4 video.
3. Name the result and tap **START FACE SWAP**.
4. Find the result in **Movies/FaceSwapPro**.

The current offline OpenCV engine exports video without the original audio track. It works best with a visible, mostly front-facing face and steady lighting.

## Build

The included GitHub Actions workflow compiles an ARM64 debug APK and uploads it as the artifact `FaceSwapPro-working-arm64-apk`.
