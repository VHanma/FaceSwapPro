# Runtime models

CI downloads `face_landmarker.task` from the official MediaPipe model bucket and records its SHA-256 before building.

Future neural swap, parsing, occlusion and restoration models must be registered in `docs/FaceSwapPro_v2_Architecture.md` with source, checksum, input size, runtime backend and license before being bundled into a release APK.
