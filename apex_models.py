"""Persistent, verified model packs for FaceSwap Pro Apex mode.

Models are downloaded once and all face processing stays on-device afterwards.
Third-party model licenses are deliberately recorded here rather than hidden in
the APK. The app does not upload source photos or target videos.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from kivy.utils import platform

ProgressCallback = Callable[[str, int], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True)
class ModelSpec:
    key: str
    filename: str
    release: str
    license_name: str
    purpose: str

    @property
    def url(self) -> str:
        return (
            "https://github.com/facefusion/facefusion-assets/releases/download/"
            f"{self.release}/{self.filename}"
        )

    @property
    def hash_url(self) -> str:
        base = self.filename.rsplit(".", 1)[0]
        return (
            "https://github.com/facefusion/facefusion-assets/releases/download/"
            f"{self.release}/{base}.hash"
        )


# GHOST is the 256px identity generator. ArcFace is required to produce the
# identity embedding consumed by GHOST and is non-commercial research weight.
# The optional parser and restorer materially improve edges and perceived
# detail, but are kept as add-ons so the user controls storage use.
MODELS = {
    "yunet": ModelSpec(
        "yunet", "yunet_2023mar.onnx", "models-3.0.0", "MIT/OpenCV model terms", "5-point face detection",
    ),
    "arcface": ModelSpec(
        "arcface", "arcface_w600k_r50.onnx", "models-3.0.0", "Non-Commercial (InsightFace)", "identity embedding",
    ),
    "ghost_converter": ModelSpec(
        "ghost_converter", "crossface_ghost.onnx", "models-3.4.0", "Apache-2.0 model family", "GHOST embedding conversion",
    ),
    "ghost": ModelSpec(
        "ghost", "ghost_1_256.onnx", "models-3.0.0", "Apache-2.0 (GHOST)", "256px neural face swap",
    ),
    "parser": ModelSpec(
        "parser", "bisenet_resnet_18.onnx", "models-3.1.0", "MIT", "face-region parsing",
    ),
    "gfpgan": ModelSpec(
        "gfpgan", "gfpgan_1.4.onnx", "models-3.0.0", "Apache-2.0", "512px face restoration",
    ),
}

PACKS = {
    "core": ("yunet", "arcface", "ghost_converter", "ghost"),
    "pro": ("yunet", "arcface", "ghost_converter", "ghost", "parser"),
    "ultra": ("yunet", "arcface", "ghost_converter", "ghost", "parser", "gfpgan"),
}


def model_dir() -> str:
    if platform == "android":
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        root = str(activity.getFilesDir().getAbsolutePath())
        path = os.path.join(root, "apex_models")
    else:
        path = os.path.join(tempfile.gettempdir(), "faceswappro_apex_models")
    os.makedirs(path, exist_ok=True)
    return path


def model_path(key: str) -> str:
    return os.path.join(model_dir(), MODELS[key].filename)


def _read_expected_hash(spec: ModelSpec) -> str:
    request = urllib.request.Request(
        spec.hash_url,
        headers={"User-Agent": "FaceSwapPro-Apex/2.0"},
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        text = response.read(8192).decode("utf-8", errors="replace")
    match = re.search(r"\b[a-fA-F0-9]{64}\b", text)
    if not match:
        raise OSError(f"Published hash is invalid for {spec.filename}")
    return match.group(0).lower()


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def is_model_ready(key: str, verify: bool = False) -> bool:
    path = model_path(key)
    if not os.path.isfile(path) or os.path.getsize(path) < 1024:
        return False
    if not verify:
        return True
    try:
        return _sha256(path) == _read_expected_hash(MODELS[key])
    except Exception:
        return False


def pack_ready(pack: str = "pro") -> bool:
    return all(is_model_ready(key) for key in PACKS[pack])


def pack_status(pack: str = "pro") -> str:
    ready = [key for key in PACKS[pack] if is_model_ready(key)]
    return f"{len(ready)}/{len(PACKS[pack])} Apex models installed"


def install_pack(
    pack: str = "pro",
    progress_cb: Optional[ProgressCallback] = None,
    cancel_cb: Optional[CancelCallback] = None,
) -> dict[str, str]:
    if pack not in PACKS:
        raise ValueError(f"Unknown Apex pack: {pack}")

    keys = PACKS[pack]
    result: dict[str, str] = {}
    total_models = len(keys)

    for model_index, key in enumerate(keys):
        if cancel_cb and cancel_cb():
            raise RuntimeError("Cancelled")

        spec = MODELS[key]
        destination = model_path(key)
        if is_model_ready(key):
            result[key] = destination
            continue

        if progress_cb:
            progress_cb(
                f"Apex {model_index + 1}/{total_models}: preparing {spec.purpose}...",
                int(model_index * 100 / total_models),
            )

        expected = _read_expected_hash(spec)
        partial = destination + ".part"
        try:
            os.remove(partial)
        except OSError:
            pass

        request = urllib.request.Request(
            spec.url,
            headers={"User-Agent": "FaceSwapPro-Apex/2.0"},
        )
        with urllib.request.urlopen(request, timeout=90) as response, open(partial, "wb") as output:
            length_header = response.headers.get("Content-Length")
            expected_bytes = int(length_header) if length_header and length_header.isdigit() else 0
            downloaded = 0
            while True:
                if cancel_cb and cancel_cb():
                    raise RuntimeError("Cancelled")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if progress_cb and expected_bytes > 0:
                    local_percent = min(99, int(downloaded * 100 / expected_bytes))
                    overall = int(
                        ((model_index + local_percent / 100.0) / total_models) * 100
                    )
                    progress_cb(
                        f"Downloading {spec.purpose}: {local_percent}%",
                        overall,
                    )

        actual = _sha256(partial)
        if actual != expected:
            try:
                os.remove(partial)
            except OSError:
                pass
            raise OSError(
                f"Security check failed for {spec.filename}: SHA-256 mismatch"
            )

        os.replace(partial, destination)
        result[key] = destination

    if progress_cb:
        progress_cb(f"Apex {pack.upper()} neural pack ready", 100)
    return result


def paths_for(pack: str = "pro") -> dict[str, str]:
    missing = [key for key in PACKS[pack] if not is_model_ready(key)]
    if missing:
        raise FileNotFoundError(
            "Apex model pack is incomplete: " + ", ".join(missing)
        )
    return {key: model_path(key) for key in PACKS[pack]}


def license_summary(pack: str = "pro") -> str:
    return "; ".join(
        f"{MODELS[key].filename}: {MODELS[key].license_name}"
        for key in PACKS[pack]
    )
