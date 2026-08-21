# FaceSwap Pro v2 model registry and licensing

Every neural weight bundled or downloaded by FaceSwap Pro v2 must have a source, role, runtime format and checksum recorded here.

## Bundled by alpha CI

### MediaPipe Face Landmarker
- Role: face detection, dense facial landmarks, blendshapes and facial transform output.
- Source: official Google MediaPipe model bucket.
- Build URL: `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task`
- Runtime: MediaPipe Tasks Vision.
- Integrity: SHA-256 generated during CI and stored alongside the build inputs.
- Distribution note: keep attribution/terms aligned with the official MediaPipe/model distribution terms used by the selected release.

### BiSeNet ResNet18 face parser
- Role: 19-class semantic face segmentation for skin, brows, eyes, eyeglasses, ears, earrings, nose, mouth, lips, neck, necklace, clothing, hair and hat.
- Upstream: `yakhyo/face-parsing`.
- Build URL: `https://github.com/yakhyo/face-parsing/releases/download/weights/resnet18.onnx`
- Input: 512x512 RGB, ImageNet normalization.
- Output: 19-class logits; argmax produces the semantic map.
- Runtime: ONNX Runtime Android.
- Upstream project license: MIT.
- Integrity: SHA-256 generated during CI and stored alongside the build inputs.

## Swapper policy

FaceSwap Pro v2 does not silently bundle a face-swap model whose weights have unclear or research-only redistribution terms. The swap engine is model-adapter based so a permitted model can be plugged in without changing tracking, masking, temporal processing, relighting or export.

Candidate families are tracked separately with their exact model terms before bundling. Research-only candidates may be supported as optional external models, but they are not treated as a commercial-clean default.

## Release gate

Before a model enters a release APK, record:
1. exact upstream repository / model page;
2. exact weight URL or immutable release asset;
3. SHA-256;
4. model input/output contract;
5. code license;
6. weight license / usage restriction;
7. attribution requirements;
8. whether redistribution inside the APK is allowed.
