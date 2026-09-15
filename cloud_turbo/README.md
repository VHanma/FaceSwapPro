# FaceSwapPro Turbo GPU

This is the server-side path for web-class video speed. The Android app should upload one source portrait and one target video, receive a job ID, poll status, then download the finished MP4.

## Why this is faster than on-device v2

- NVIDIA CUDA / TensorRT instead of phone CPU ONNX.
- Models and assets live on the GPU worker instead of loading hundreds of MB on the phone.
- FaceFusion runs with up to 16 execution threads.
- TensorRT engine plans are cached on GPU storage.
- H.264 is encoded with NVENC rather than Android software/YUV conversion.
- The phone does no frame extraction, face inference, or recompression in Turbo mode.

## API contract

`POST /v1/video-swap` as multipart form data:

- `source`: source portrait image
- `target`: target video
- `quality`: `fast`, `pro` (default), or `ultra`
- optional HTTP header `Authorization: Bearer <TURBO_API_KEY>`

Response is HTTP 202:

```json
{
  "job_id": "...",
  "status": "queued",
  "status_url": "/v1/jobs/<id>",
  "progress": 12
}
```

Poll `GET /v1/jobs/<id>`. When `status` becomes `completed`, download `GET /v1/jobs/<id>/result`.

## Quality profiles

- **fast**: HyperSwap 1A 256, 256 pixel boost, face swap only.
- **pro**: HyperSwap 1B 256, 384 pixel boost, face enhancer.
- **ultra**: HyperSwap 1C 256, 512 pixel boost, expression restorer + face enhancer.

The worker asks FaceFusion for TensorRT first and CUDA second, uses tolerant VRAM caching, and uses `h264_nvenc` with an ultrafast encode preset.

## Deployment target

Build `cloud_turbo/Dockerfile` on an NVIDIA GPU host such as a persistent RunPod GPU Pod, a GPU VM, or another Docker host with NVIDIA Container Toolkit. For low latency, keep at least one worker warm instead of cold-starting a serverless GPU for every request.

Recommended starting GPU: RTX 4090 / L4 / A10-class or better. Premium-site behavior comes primarily from keeping GPUs hot and avoiding phone-side inference.

Environment variables:

- `TURBO_API_KEY`: bearer token required by the Android client. Leave empty only for private-network testing.
- `TURBO_JOB_WORKERS`: default `1`. Increase only after measuring VRAM use.
- `TURBO_MAX_UPLOAD_MB`: default `1500`.
- `TURBO_TTL_SECONDS`: default `7200`, after which finished temporary media can be deleted.

## Model terms

FaceFusion code and individual face-swap/enhancement models have separate licenses. Review the current upstream licenses before commercial distribution or offering this worker as a paid service.
