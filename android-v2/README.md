# FaceSwap Pro v2

Native Android rebuild of FaceSwap Pro.

## Alpha 1 target
- ARM64 Android app using Kotlin + Compose.
- MediaPipe VIDEO-mode 478-point face tracking.
- C++20 JNI engine boundary.
- Multi-photo Identity Vault UI/contracts.
- Fast / Balanced / Movie quality profiles.
- CI-built installable debug APK.

The old Python/OpenCV engine remains in the repository while v2 is built beside it. It is not the quality target for v2.

## Build
CI installs API 37, NDK 28.2, CMake 3.22.1, downloads Google's Face Landmarker model into app assets, then runs `gradle :app:assembleDebug`.

## Model rule
Neural swap/restoration weights are added only after their source, checksum and license are recorded. Runtime adapters are intentionally model-swappable so v2 is not trapped behind one face-swap model.
