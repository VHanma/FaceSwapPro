# FaceSwap Pro v2 native architecture

## Goal
Replace the legacy Haar + hand-guessed landmark + Delaunay triangle warp engine with a native Android pipeline designed for stable video face replacement.

## Implemented on `faceswappro-v2-native`
- Kotlin / Jetpack Compose application shell.
- Android API 37 compile, API 36 target, ARM64-first.
- MediaPipe Tasks Vision Face Landmarker in VIDEO mode.
- 478-point landmark path plus blendshape and facial transform outputs enabled.
- Real sampled-video tracking quality probe with detection-rate and landmark-count gates.
- C++20/JNI engine boundary.
- ONNX Runtime Android backend and on-device runtime/provider probe.
- Fast / Balanced / Movie quality contracts.
- Identity Vault contracts for multi-image, pose-aware source selection.
- Real BiSeNet ResNet18 ONNX semantic parser with 19 CelebAMask-HQ regions.
- Semantic mask probe for skin, hair, eyes, mouth/lips, glasses and protected foreground.
- Separate semantic masks for identity-bearing pixels, mouth interior and foreground protection.
- Stage contracts for neural swap, occlusion, temporal stabilization, relighting, restoration and quality-gated rerendering.
- CI downloads and hashes the official tracking model and MIT face-parsing model, builds ARM64 APK, then verifies the native library/models are packaged.

## Runtime plan
1. **Tracking:** MediaPipe Face Landmarker, VIDEO mode. Tracking confidence and persistent timestamps reduce detector churn between frames.
2. **Identity:** multiple source images are scored for yaw/pitch/roll, sharpness, occlusion and identity confidence. Per target frame, the vault selects or fuses the best pose-compatible identity evidence.
3. **Neural swap:** swappable model adapter. Primary deployment target remains ncnn/Vulkan; ONNX Runtime is already wired as the broad compatibility backend.
4. **Semantic compositor:** the implemented 19-region parser replaces the single blurred oval. Hair, hats, eyeglasses and earrings can be protected independently; mouth interior is isolated from the identity composite.
5. **Occlusion:** semantic masks provide known facial foreground classes. A dedicated occluder model/temporal foreground pass will handle arbitrary hands, microphones and other objects crossing the face.
6. **Temporal system:** scene-cut reset, track continuity, landmark filtering, previous-mask propagation and neighboring-frame quality signals.
7. **Relighting:** low-frequency spatial illumination is transferred without overwriting identity detail.
8. **Restoration:** restoration occurs after identity synthesis and compositing, with strength constrained by identity similarity.
9. **Quality gate:** Movie mode scores identity, geometry, mask, temporal consistency and lighting. Frames below the threshold become rerender candidates.
10. **Mastering:** preserve source timing/audio, then export an HQ H.264/H.265 master depending on device/backend support.

## Quality modes
### Fast
256px internal face crop, minimal temporal radius, no expensive rerender loop.

### Balanced
512px crop, semantic masks, temporal refinement, relighting and one restoration pass.

### Movie
512px or higher model-dependent crop, multi-frame temporal window, occlusion refinement, camera-character matching, stronger restoration and quality-triggered rerendering.

## Model policy
See `docs/Model_Licensing.md`. Models are registered with source, hash and usage terms before release packaging. This keeps the architecture replaceable instead of trapping the APK behind one swapper.

## Remaining implementation gates
1. Get a green CI APK build and run the native/runtime/tracking/masking probes on-device.
2. Implement Identity Vault embeddings and automatic pose scoring.
3. Integrate the first face-swap model behind the `SwapEngine` contract.
4. Add arbitrary-object occlusion refinement beyond the current semantic face classes.
5. Add temporal mask/geometry propagation and scene-cut handling.
6. Add spatial relighting and restoration passes.
7. Add per-frame quality scoring and Movie-mode rerender controller.
8. Replace probe-only processing with full video decode → process → audio-preserving master export.
