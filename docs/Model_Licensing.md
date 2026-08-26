# FaceSwap Pro v2 model registry and licensing

Every neural weight bundled or downloaded by FaceSwap Pro v2 must have a source, role, runtime format and checksum recorded here.

## Bundled by alpha CI

### MediaPipe Face Landmarker
- Role: face detection, dense facial landmarks, 52 expression blendshapes and 4x4 facial transform output.
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

### EdgeFace XXS
- Role: lightweight 512-D identity embedding for multi-photo Identity Vault fusion, source consistency and future rendered-frame QC.
- Upstream: `yakhyo/edgeface-onnx` / EdgeFace.
- Build URL: `https://github.com/yakhyo/edgeface-onnx/releases/download/weights/edgeface_xxs.onnx`
- Input: aligned 112x112 RGB, `(value - 127.5) / 127.5`.
- Runtime: ONNX Runtime Android.
- Upstream ONNX project license: MIT.
- Integrity: SHA-256 generated during CI and stored alongside the build inputs.

## Downloadable Movie Neural Pack

The Movie pack is deliberately downloaded after install instead of silently embedding several hundred MiB of restricted/research weights into the base APK. Every file is size-checked and SHA-256-verified before the pack is accepted. Inference is offline after installation.

### ArcFace W600K R50
- Role: source identity representation expected by the SimSwap model family.
- Upstream/vendor: InsightFace.
- Download: `https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/arcface_w600k_r50.onnx`
- SHA-256: `f1f79dc3b0b79a69f94799af1fffebff09fbd78fd96a275fd8f0cbbea23270d1`
- Size: 174,388,474 bytes.
- Usage classification in current FaceFusion model metadata: non-commercial.

### CrossFace SimSwap converter
- Role: converts the ArcFace representation into the embedding contract expected by the packaged SimSwap ONNX graph.
- Download: `https://github.com/facefusion/facefusion-assets/releases/download/models-3.4.0/crossface_simswap.onnx`
- SHA-256: `6452a261ec30cc30afdbe4a426d82c3b10a476f2df794e3494071c02574e6829`
- Size: 22,083,800 bytes.
- Release policy: treat as part of the restricted SimSwap research path unless/until separate weight terms are independently cleared.

### SimSwap unofficial 512
- Role: 512x512 neural identity synthesis.
- Upstream/vendor: neuralchen / SimSwap family.
- Download: `https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/simswap_unofficial_512.onnx`
- SHA-256: `fe805d1ce7d9e66322e2a8811f593a821e7d92f9ff861dd233794bdb2bb7a586`
- Size: 239,249,034 bytes.
- Usage classification in current FaceFusion model metadata: non-commercial.

### DeepFaceLab XSeg v3
- Role: arbitrary foreground/occlusion visibility mask so hands, phones, microphones and other crossing objects can remain in front of the replacement face.
- Upstream/vendor: DeepFaceLab XSeg; current FaceFusion masker model `xseg_3`.
- Download: `https://github.com/facefusion/facefusion-assets/releases/download/models-3.2.0/xseg_3.onnx`
- SHA-256: `48ccd7e8541e159a5a754ec9e62df2f12065f7df8f9af842c1750342c6533559`
- Size: 70,327,709 bytes.
- Current FaceFusion metadata identifies the XSeg model family as GPL-3.0.
- Runtime contract used by v2: 256x256 NHWC BGR float input in `[0,1]`, ONNX Runtime Android.

## Swapper policy

The native pipeline is model-adapter based. Tracking, masking, Identity Vault, temporal stabilization, spatial relighting and export are kept independent from the swapper weights so a stronger or differently licensed synthesis model can replace SimSwap without another architecture rewrite.

Research/non-commercial candidates are never described as commercial-clean. The app records exact files and hashes and keeps their model terms distinct from the permissively licensed app-side code/components.

## Release gate

Before any additional model enters a release APK or downloadable pack, record:
1. exact upstream repository / model page;
2. exact weight URL or immutable release asset;
3. SHA-256;
4. model input/output contract;
5. code license;
6. weight license / usage restriction;
7. attribution requirements;
8. whether redistribution inside the APK is allowed.
