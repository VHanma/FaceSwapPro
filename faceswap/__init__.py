"""Android runtime fixes for the expression-aware FaceSwap Pro engine.

Python imports this package before the sibling ``faceswap.py`` module. The
package loads that engine privately, then supplies Android-safe FFmpeg lookup
and non-blocking error capture. Everything remains offline.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional


_BASE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "faceswap.py",
)
_SPEC = importlib.util.spec_from_file_location(
    "_faceswappro_expression_engine",
    _BASE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("Could not load the FaceSwap Pro expression engine")

_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

ProgressCallback = _BASE.ProgressCallback
CancelCallback = _BASE.CancelCallback
Rect = _BASE.Rect
VideoInfo = _BASE.VideoInfo
BaseFaceSwapper = _BASE.FaceSwapper


def _android_native_library_dir() -> Optional[str]:
    """Return the APK native-library directory when running on Android."""
    try:
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        path = activity.getApplicationInfo().nativeLibraryDir
        return str(path) if path else None
    except Exception:
        return None


class FaceSwapper(BaseFaceSwapper):
    """Expression engine with Android-safe FFmpeg process handling."""

    @staticmethod
    def _ffmpeg_binary() -> str:
        native_dir = _android_native_library_dir()
        candidates: list[str] = []

        if native_dir:
            candidates.extend(
                [
                    os.path.join(native_dir, "libffmpegbin.so"),
                    os.path.join(native_dir, "ffmpeg"),
                ]
            )

        private_dir = os.environ.get("ANDROID_PRIVATE")
        if private_dir:
            candidates.extend(
                [
                    os.path.join(private_dir, "lib", "libffmpegbin.so"),
                    os.path.join(private_dir, "libffmpegbin.so"),
                    os.path.join(private_dir, "ffmpeg"),
                ]
            )

        discovered = shutil.which("ffmpeg")
        if discovered:
            candidates.append(discovered)

        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                try:
                    os.chmod(candidate, os.stat(candidate).st_mode | 0o100)
                except OSError:
                    pass
                return candidate

        raise RuntimeError(
            "The bundled FFmpeg executable was not found in this APK. "
            "Rebuild with ffmpeg,av_codecs enabled."
        )

    @classmethod
    def _open_ffmpeg_writer(
        cls,
        output_path: str,
        source_video: str,
        info: VideoInfo,
    ) -> subprocess.Popen:
        ffmpeg = cls._ffmpeg_binary()
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s:v",
            f"{info.width}x{info.height}",
            "-r",
            f"{info.fps:.6f}",
            "-i",
            "pipe:0",
            "-i",
            source_video,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-shortest",
            output_path,
        ]

        # A TemporaryFile drains FFmpeg continuously at OS level. A PIPE can
        # fill during long/corrupt videos and freeze both encoding and cancel.
        stderr_file = tempfile.TemporaryFile()
        env = os.environ.copy()
        native_dir = _android_native_library_dir()
        if native_dir:
            existing = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = (
                native_dir if not existing else native_dir + os.pathsep + existing
            )

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
                bufsize=0,
                env=env,
            )
        except Exception:
            stderr_file.close()
            raise

        setattr(process, "_faceswappro_stderr_file", stderr_file)
        if process.stdin is None:
            process.kill()
            stderr_file.close()
            raise RuntimeError("FFmpeg encoder pipe could not be opened")
        return process

    @staticmethod
    def _finish_ffmpeg(process: subprocess.Popen) -> tuple[int, str]:
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass

        timed_out = False
        try:
            code = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            code = process.wait()

        stderr_file = getattr(process, "_faceswappro_stderr_file", None)
        stderr = b""
        if stderr_file is not None:
            try:
                stderr_file.flush()
                stderr_file.seek(0)
                stderr = stderr_file.read()
            except OSError:
                stderr = b""
            finally:
                try:
                    stderr_file.close()
                except OSError:
                    pass

        if timed_out:
            return code or 1, "FFmpeg timed out while finalizing the MP4"
        return code, stderr.decode("utf-8", errors="replace").strip()


def process_video(
    source_path: str,
    video_path: str,
    output_path: str,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_cb: Optional[CancelCallback] = None,
) -> tuple[bool, str]:
    return FaceSwapper().process_video(
        source_path,
        video_path,
        output_path,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
