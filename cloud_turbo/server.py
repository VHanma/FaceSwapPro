#!/usr/bin/env python3
"""FaceSwapPro Turbo GPU service.

A small async FastAPI front end around FaceFusion. It is designed for an NVIDIA
GPU container/pod where models and TensorRT engine caches stay on fast local
disk. Android uploads source + target once, polls a job, then downloads MP4.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(os.getenv("TURBO_WORK_DIR", "/tmp/faceswappro-turbo"))
FACEFUSION = Path(os.getenv("FACEFUSION_DIR", "/opt/facefusion"))
API_KEY = os.getenv("TURBO_API_KEY", "").strip()
MAX_UPLOAD_MB = int(os.getenv("TURBO_MAX_UPLOAD_MB", "1500"))
WORKERS = max(1, int(os.getenv("TURBO_JOB_WORKERS", "1")))
TTL_SECONDS = max(900, int(os.getenv("TURBO_TTL_SECONDS", "7200")))
ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="FaceSwapPro Turbo", version="3.0")
executor = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="turbo-gpu")
jobs: Dict[str, dict] = {}


class JobReply(BaseModel):
    job_id: str
    status: str
    status_url: str
    result_url: Optional[str] = None
    progress: int = 0
    detail: str = ""


def auth(authorization: Optional[str]) -> None:
    if not API_KEY:
        return
    token = (authorization or "").removeprefix("Bearer ").strip()
    if token != API_KEY:
        raise HTTPException(401, "Invalid Turbo API key")


def job_reply(job_id: str) -> JobReply:
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "Unknown job")
    result_url = f"/v1/jobs/{job_id}/result" if j["status"] == "completed" else None
    return JobReply(job_id=job_id, status=j["status"], status_url=f"/v1/jobs/{job_id}",
                    result_url=result_url, progress=j.get("progress", 0), detail=j.get("detail", ""))


async def save_upload(upload: UploadFile, dst: Path) -> None:
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    with dst.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                out.close()
                dst.unlink(missing_ok=True)
                raise HTTPException(413, f"Upload exceeds {MAX_UPLOAD_MB} MB")
            out.write(chunk)


def ff_command(source: Path, target: Path, output: Path, quality: str) -> list[str]:
    quality = quality.lower().strip()
    if quality == "fast":
        swapper = "hyperswap_1a_256"
        boost = "256x256"
        processors = ["face_swapper"]
        out_quality = "90"
    elif quality == "ultra":
        swapper = "hyperswap_1c_256"
        boost = "512x512"
        processors = ["face_swapper", "expression_restorer", "face_enhancer"]
        out_quality = "100"
    else:
        swapper = "hyperswap_1b_256"
        boost = "384x384"
        processors = ["face_swapper", "face_enhancer"]
        out_quality = "96"

    cmd = [
        "python3", str(FACEFUSION / "facefusion.py"), "headless-run",
        "-s", str(source), "-t", str(target), "-o", str(output),
        "--processors", *processors,
        "--face-swapper-model", swapper,
        "--face-swapper-pixel-boost", boost,
        "--face-mask-types", "box", "occlusion",
        "--execution-providers", "tensorrt", "cuda",
        "--execution-thread-count", "16",
        "--video-memory-strategy", "tolerant",
        "--output-video-encoder", "h264_nvenc",
        "--output-video-preset", "ultrafast",
        "--output-video-quality", out_quality,
        "--log-level", "info",
    ]
    return cmd


def run_job(job_id: str, source: Path, target: Path, output: Path, quality: str) -> None:
    j = jobs[job_id]
    j.update(status="running", progress=20, detail="GPU worker started")
    started = time.time()
    try:
        env = os.environ.copy()
        env.setdefault("ORT_TENSORRT_ENGINE_CACHE_ENABLE", "1")
        env.setdefault("ORT_TENSORRT_CACHE_PATH", "/opt/trt-cache")
        Path(env["ORT_TENSORRT_CACHE_PATH"]).mkdir(parents=True, exist_ok=True)
        cmd = ff_command(source, target, output, quality)
        j.update(progress=35, detail="Face tracking + GPU synthesis")
        proc = subprocess.run(cmd, cwd=FACEFUSION, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            tail = (proc.stdout or "")[-5000:]
            raise RuntimeError(f"FaceFusion exit={proc.returncode}\n{tail}")
        j.update(status="completed", progress=100,
                 detail=f"Ready in {time.time() - started:.1f}s", finished=time.time())
    except Exception as e:
        j.update(status="failed", progress=100, detail=str(e), finished=time.time())


@app.on_event("startup")
def startup() -> None:
    # Prime model downloads and TensorRT caches in the image/startup layer where possible.
    (Path("/opt/trt-cache")).mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health() -> dict:
    active = sum(1 for j in jobs.values() if j["status"] in {"queued", "running"})
    return {"ok": True, "gpu": os.getenv("NVIDIA_VISIBLE_DEVICES", "unknown"),
            "active_jobs": active, "workers": WORKERS}


@app.post("/v1/video-swap", response_model=JobReply, status_code=202)
async def video_swap(source: UploadFile = File(...), target: UploadFile = File(...),
                     quality: str = Form("pro"), authorization: Optional[str] = Header(None)) -> JobReply:
    auth(authorization)
    job_id = uuid.uuid4().hex
    job_dir = ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    src_ext = Path(source.filename or "source.jpg").suffix or ".jpg"
    tgt_ext = Path(target.filename or "target.mp4").suffix or ".mp4"
    src = job_dir / f"source{src_ext}"
    tgt = job_dir / f"target{tgt_ext}"
    out = job_dir / "result.mp4"
    jobs[job_id] = {"status": "uploading", "progress": 2, "detail": "Receiving media",
                    "created": time.time(), "result": out}
    await save_upload(source, src)
    jobs[job_id].update(progress=8, detail="Source received")
    await save_upload(target, tgt)
    jobs[job_id].update(status="queued", progress=12, detail="Queued for GPU")
    executor.submit(run_job, job_id, src, tgt, out, quality)
    return job_reply(job_id)


@app.get("/v1/jobs/{job_id}", response_model=JobReply)
def status(job_id: str, authorization: Optional[str] = Header(None)) -> JobReply:
    auth(authorization)
    return job_reply(job_id)


@app.get("/v1/jobs/{job_id}/result")
def result(job_id: str, authorization: Optional[str] = Header(None)):
    auth(authorization)
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "Unknown job")
    if j["status"] != "completed":
        raise HTTPException(409, f"Job is {j['status']}")
    path: Path = j["result"]
    if not path.exists():
        raise HTTPException(410, "Result expired")
    return FileResponse(path, media_type="video/mp4", filename="FaceSwapPro_Turbo.mp4")


@app.post("/v1/cleanup")
def cleanup(authorization: Optional[str] = Header(None)) -> dict:
    auth(authorization)
    now = time.time()
    removed = 0
    for job_id, j in list(jobs.items()):
        stamp = j.get("finished", j.get("created", now))
        if now - stamp > TTL_SECONDS and j["status"] not in {"queued", "running", "uploading"}:
            shutil.rmtree(ROOT / job_id, ignore_errors=True)
            jobs.pop(job_id, None)
            removed += 1
    return {"removed": removed}
