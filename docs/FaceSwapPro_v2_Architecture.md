# FaceSwap Pro v2 native architecture

## Goal
Replace the legacy Haar + hand-guessed landmark + Delaunay triangle warp engine with a native Android pipeline designed for stable video face replacement.

## Current alpha1 foundation
- Kotlin / Jetpack Compose application shell.
- Android API 37 compile, API 36 target, ARM64-first.
- MediaPipe Tasks Vision 0.10.35 Face Landmarker in VIDEO mode.
- 478-point landmark output path plus blendshape and facial transform outputs enabled.
- C++20/JNI engine boundary.
- Fast / Balanced / Movie quality contracts.
- Identity Vault contracts for multi-image, pose-aware source selection.
- Stage contracts for neural swap, semantic mask, occlusion, temporal stabilization, relighting, restoration and quality-gated rerendering.

## Runtime plan
1. **Tracking:** MediaPipe Face Landmarker, VIDEO mode. Tracking confidence and persistent timestamps are used to reduce detector churn between frames.
2. **Identity:** multiple source images are scored for yaw/pitch/roll, sharpness, occlusion and identity confidence. Per target frame, the vault selects or fuses the best pose-compatible identity evidence.
3. **Neural swap:** swappable model adapter. Primary deployment target is ncnn/Vulkan; ONNX Runtime remains the compatibility backend.
4. **Semantic compositor:** face parsing produces region masks rather than one blurred oval. Mouth interior, eye highlights, facial hair, hair, glasses and foreground occluders remain independently controllable.
5. **Temporal system:** scene-cut reset, track continuity, landmark filtering, previous-mask propagation and neighboring-frame quality signals.
6. **Relighting:** low-frequency spatial illumination is transferred without overwriting identity detail.
7. **Restoration:** restoration occurs after identity synthesis and compositing, with strength constrained by identity similarity.
8. **Quality gate:** Movie mode scores identity, geometry, mask, temporal consistency and lighting. Frames below the threshold are rerender candidates.
9. **Mastering:** preserve source timing/audio, then export an HQ H.264/H.265 master depending on device/backend support.

## Quality modes
### Fast
256px internal face crop, minimal temporal radius, no expensive rerender loop.

### Balanced
512px crop, semantic masks, temporal refinement, relighting and one restoration pass.

### Movie
512px or higher model-dependent crop, multi-frame temporal window, occlusion refinement, camera-character matching, stronger restoration and quality-triggered rerendering.

## Model policy
Model binaries are not silently committed. Every model must have a documented source, hash, license and intended commercial/non-commercial usage before release packaging. This also lets us replace the swapper without rewriting the app.

## Next implementation gates
1. Compile/install alpha1 and validate the JNI self-test.
2. Decode video frames into MediaPipe-compatible images and export a tracking-preview video.
3. Build Identity Vault embeddings and pose scoring.
4. Integrate the first licensed neural swap model behind the `SwapEngine` contract.
5. Add semantic parsing and occlusion masks.
6. Add temporal/relight/restoration passes.
7. Add per-frame quality scoring and Movie-mode rerender controller.
