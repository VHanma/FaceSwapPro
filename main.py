#!/usr/bin/env python3
"""FaceSwap Pro APEX Android UI. Local neural face swapping, no media upload."""

from __future__ import annotations

import mimetypes
import os
import re
import tempfile
import threading
from typing import Optional

from kivy.app import App
from kivy.clock import mainthread
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform

REQUEST_SOURCE = 4101
REQUEST_VIDEO = 4102


def _android_activity():
    from jnius import autoclass
    return autoclass("org.kivy.android.PythonActivity").mActivity


def _cache_dir() -> str:
    if platform == "android":
        return str(_android_activity().getCacheDir().getAbsolutePath())
    return tempfile.gettempdir()


def _delete_quietly(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _uri_suffix(uri, fallback: str) -> str:
    if platform != "android":
        return fallback
    try:
        mime = _android_activity().getContentResolver().getType(uri)
        if mime:
            mime = str(mime)
            if mime == "image/jpeg":
                return ".jpg"
            if mime == "video/mp4":
                return ".mp4"
            return mimetypes.guess_extension(mime) or fallback
    except Exception:
        pass
    return fallback


def _copy_content_uri(uri, suffix: str) -> str:
    resolver = _android_activity().getContentResolver()
    input_stream = resolver.openInputStream(uri)
    if input_stream is None:
        raise OSError("Android could not open the selected file")

    fd, destination = tempfile.mkstemp(
        prefix="faceswappro_", suffix=suffix, dir=_cache_dir()
    )
    os.close(fd)
    buffer = bytearray(1024 * 1024)
    try:
        with open(destination, "wb") as output:
            while True:
                count = input_stream.read(buffer)
                if count is None:
                    break
                count = int(count)
                if count < 0:
                    break
                if count == 0:
                    continue
                output.write(buffer[:count])
    except Exception:
        _delete_quietly(destination)
        raise
    finally:
        input_stream.close()

    if not os.path.isfile(destination) or os.path.getsize(destination) <= 0:
        _delete_quietly(destination)
        raise OSError("The selected file copied as an empty file")
    return destination


def _publish_video(local_path: str, display_name: str) -> str:
    """Transactional MediaStore write without the old PyJNIus int-overload bug."""
    if platform != "android":
        return local_path
    if not os.path.isfile(local_path) or os.path.getsize(local_path) <= 0:
        raise OSError("APEX did not create a usable output video")

    from jnius import autoclass

    activity = _android_activity()
    resolver = activity.getContentResolver()
    ContentValues = autoclass("android.content.ContentValues")
    MediaStoreVideo = autoclass("android.provider.MediaStore$Video$Media")
    MediaColumns = autoclass("android.provider.MediaStore$MediaColumns")
    Environment = autoclass("android.os.Environment")
    Integer = autoclass("java.lang.Integer")

    values = ContentValues()
    values.put(MediaColumns.DISPLAY_NAME, display_name)
    values.put(MediaColumns.MIME_TYPE, "video/mp4")
    values.put(
        MediaColumns.RELATIVE_PATH,
        Environment.DIRECTORY_MOVIES + "/FaceSwapPro",
    )
    # Box explicitly so PyJNIus cannot choose the wrong ContentValues.put overload.
    values.put(MediaColumns.IS_PENDING, Integer.valueOf(1))

    uri = resolver.insert(MediaStoreVideo.EXTERNAL_CONTENT_URI, values)
    if uri is None:
        raise OSError("Android could not create the result in Movies")

    try:
        output_stream = resolver.openOutputStream(uri)
        if output_stream is None:
            raise OSError("Android could not open the result file")
        try:
            with open(local_path, "rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output_stream.write(bytearray(chunk))
            output_stream.flush()
        finally:
            output_stream.close()

        ready = ContentValues()
        ready.put(MediaColumns.IS_PENDING, Integer.valueOf(0))
        if int(resolver.update(uri, ready, None, None)) < 1:
            raise OSError("Android could not finalize the saved video")
        return str(uri.toString())
    except Exception:
        try:
            resolver.delete(uri, None, None)
        except Exception:
            pass
        raise


class FaceSwapRoot(ScrollView):
    def __init__(self, **kwargs):
        super().__init__(do_scroll_x=False, **kwargs)
        self.source_image_path: Optional[str] = None
        self.target_video_path: Optional[str] = None
        self._cancel_event = threading.Event()
        self._working = False

        self.content = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(18), dp(20), dp(18), dp(32)],
            spacing=dp(11),
        )
        self.content.bind(minimum_height=self.content.setter("height"))
        self.add_widget(self.content)
        self._build_ui()

        if platform == "android":
            from android import activity
            activity.bind(on_activity_result=self._on_activity_result)

    @staticmethod
    def _fixed_label(text: str, height: float, **kwargs) -> Label:
        label = Label(
            text=text,
            size_hint_y=None,
            height=height,
            halign="left",
            valign="middle",
            **kwargs,
        )
        label.bind(
            width=lambda instance, value: setattr(
                instance, "text_size", (max(dp(20), value), None)
            )
        )
        return label

    @staticmethod
    def _button(text: str, background, height=dp(54)) -> Button:
        return Button(
            text=text,
            size_hint_y=None,
            height=height,
            background_normal="",
            background_color=background,
            color=(1, 1, 1, 1),
            font_size=dp(16),
        )

    def _model_status_text(self) -> str:
        try:
            from apex_models import pack_ready, pack_status
            if pack_ready("ultra"):
                return "APEX ULTRA ready: neural swap + semantic mask + 512px restoration"
            if pack_ready("pro"):
                return "APEX PRO ready: neural swap + semantic face mask"
            return "APEX models needed: " + pack_status("pro")
        except Exception as exc:
            return f"APEX model status unavailable: {exc}"

    def _build_ui(self) -> None:
        self.content.add_widget(
            Label(
                text="[b]FaceSwap Pro APEX[/b]",
                markup=True,
                size_hint_y=None,
                height=dp(60),
                font_size=dp(30),
                color=(0.32, 0.88, 1.0, 1),
            )
        )
        self.content.add_widget(
            self._fixed_label(
                "Quality-first neural face swapping. Models download once; your photos and videos stay on this phone.",
                dp(64),
                font_size=dp(13),
                color=(0.74, 0.79, 0.91, 1),
            )
        )

        self.install_pro_button = self._button(
            "INSTALL / REPAIR APEX PRO MODELS", (0.36, 0.28, 0.82, 1), dp(58)
        )
        self.install_pro_button.bind(on_release=lambda *_: self.install_models("pro"))
        self.content.add_widget(self.install_pro_button)

        self.install_ultra_button = self._button(
            "ADD ULTRA 512px RESTORATION", (0.52, 0.25, 0.70, 1), dp(52)
        )
        self.install_ultra_button.bind(on_release=lambda *_: self.install_models("ultra"))
        self.content.add_widget(self.install_ultra_button)

        self.model_label = self._fixed_label(
            self._model_status_text(),
            dp(56),
            font_size=dp(12),
            color=(0.80, 0.75, 1.0, 1),
        )
        self.content.add_widget(self.model_label)

        self.source_button = self._button(
            "1. Choose source face photo", (0.10, 0.48, 0.86, 1)
        )
        self.source_button.bind(on_release=self.choose_source)
        self.content.add_widget(self.source_button)
        self.source_label = self._fixed_label(
            "No source photo selected", dp(30), font_size=dp(12), color=(0.55, 0.58, 0.68, 1)
        )
        self.content.add_widget(self.source_label)

        self.video_button = self._button(
            "2. Choose target video", (0.08, 0.62, 0.39, 1)
        )
        self.video_button.bind(on_release=self.choose_video)
        self.content.add_widget(self.video_button)
        self.video_label = self._fixed_label(
            "No target video selected", dp(30), font_size=dp(12), color=(0.55, 0.58, 0.68, 1)
        )
        self.content.add_widget(self.video_label)

        self.content.add_widget(
            self._fixed_label("Result filename", dp(28), font_size=dp(13), color=(0.84, 0.87, 0.98, 1))
        )
        self.output_name = TextInput(
            text="apex_faceswap",
            multiline=False,
            size_hint_y=None,
            height=dp(48),
            padding=[dp(12), dp(12), dp(12), dp(8)],
            background_normal="",
            background_active="",
            background_color=(0.10, 0.11, 0.17, 1),
            foreground_color=(1, 1, 1, 1),
            cursor_color=(0.30, 0.86, 1.0, 1),
        )
        self.content.add_widget(self.output_name)

        self.run_button = self._button(
            "START APEX FACE SWAP", (0.82, 0.16, 0.20, 1), dp(68)
        )
        self.run_button.font_size = dp(20)
        self.run_button.bind(on_release=self.start_swap)
        self.content.add_widget(self.run_button)

        self.cancel_button = self._button("Cancel", (0.30, 0.31, 0.38, 1), dp(48))
        self.cancel_button.disabled = True
        self.cancel_button.bind(on_release=self.cancel_swap)
        self.content.add_widget(self.cancel_button)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(18))
        self.content.add_widget(self.progress)

        self.status = Label(
            text="Ready. A well-lit source photo with visible eyes and mouth gives the strongest identity lock.",
            size_hint_y=None,
            height=dp(96),
            font_size=dp(13),
            color=(0.75, 0.94, 0.78, 1),
            halign="left",
            valign="top",
        )
        self.status.bind(
            width=lambda instance, value: setattr(instance, "text_size", (max(dp(20), value), None))
        )
        self.status.bind(
            texture_size=lambda instance, value: setattr(instance, "height", max(dp(96), value[1] + dp(18)))
        )
        self.content.add_widget(self.status)

        self.content.add_widget(
            self._fixed_label(
                "APEX uses neural identity transfer, real 5-point alignment, semantic face masking, temporal anti-flicker, relighting and optional 512px restoration. Audio is preserved.",
                dp(112),
                font_size=dp(12),
                color=(0.60, 0.64, 0.75, 1),
            )
        )

    def install_models(self, pack: str) -> None:
        if self._working:
            return
        self._cancel_event.clear()
        self._working = True
        self._set_controls(True)
        self._set_progress(0)
        self._set_status(
            "Downloading verified APEX model files. This happens once; processing remains local afterward.",
            False,
        )
        threading.Thread(target=self._install_models_worker, args=(pack,), daemon=True).start()

    def _install_models_worker(self, pack: str) -> None:
        try:
            from apex_models import install_pack, license_summary
            install_pack(
                pack,
                progress_cb=self._engine_progress,
                cancel_cb=self._cancel_event.is_set,
            )
            self._refresh_model_label()
            self._set_status(
                f"APEX {pack.upper()} ready. Third-party model terms: {license_summary(pack)}",
                False,
            )
        except Exception as exc:
            if str(exc) == "Cancelled":
                self._set_status("Model installation cancelled.", False)
            else:
                self._set_status(f"Model installation failed: {type(exc).__name__}: {exc}", True)
        finally:
            self._finish_work()

    @mainthread
    def _refresh_model_label(self) -> None:
        self.model_label.text = self._model_status_text()

    def _open_picker(self, mime_type: str, request_code: int) -> None:
        if platform != "android":
            self._set_status("File selection is enabled in the Android APK.", True)
            return
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        intent.setType(mime_type)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        _android_activity().startActivityForResult(intent, request_code)

    def choose_source(self, *_args) -> None:
        self._open_picker("image/*", REQUEST_SOURCE)

    def choose_video(self, *_args) -> None:
        self._open_picker("video/*", REQUEST_VIDEO)

    def _on_activity_result(self, request_code, result_code, data) -> None:
        if int(result_code) != -1 or data is None:
            return
        uri = data.getData()
        if uri is None:
            return
        if int(request_code) == REQUEST_SOURCE:
            self._set_source_label("Loading source photo...", False)
            threading.Thread(
                target=self._load_selection,
                args=(uri, _uri_suffix(uri, ".img"), "source"),
                daemon=True,
            ).start()
        elif int(request_code) == REQUEST_VIDEO:
            self._set_video_label("Loading target video...", False)
            threading.Thread(
                target=self._load_selection,
                args=(uri, _uri_suffix(uri, ".video"), "video"),
                daemon=True,
            ).start()

    def _load_selection(self, uri, suffix: str, kind: str) -> None:
        try:
            path = _copy_content_uri(uri, suffix)
            if kind == "source":
                old = self.source_image_path
                self.source_image_path = path
                _delete_quietly(old)
                self._set_source_label("Source photo ready", True)
            else:
                old = self.target_video_path
                self.target_video_path = path
                _delete_quietly(old)
                mb = os.path.getsize(path) / (1024 * 1024)
                self._set_video_label(f"Target video ready ({mb:.1f} MB)", True)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if kind == "source":
                self._set_source_label(f"Could not load photo: {message}", False)
            else:
                self._set_video_label(f"Could not load video: {message}", False)

    @staticmethod
    def _safe_filename(text: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip()).strip("._")
        if cleaned.lower().endswith(".mp4"):
            cleaned = cleaned[:-4].rstrip("._")
        return (cleaned or "apex_faceswap")[:80] + ".mp4"

    def start_swap(self, *_args) -> None:
        if self._working:
            return
        if not self.source_image_path:
            self._set_status("Choose a source face photo first.", True)
            return
        if not self.target_video_path:
            self._set_status("Choose a target video first.", True)
            return
        try:
            from apex_models import pack_ready
            if not pack_ready("pro"):
                self._set_status("Install the APEX PRO model pack first.", True)
                return
        except Exception as exc:
            self._set_status(f"Could not check APEX models: {exc}", True)
            return

        filename = self._safe_filename(self.output_name.text)
        source_path = self.source_image_path
        video_path = self.target_video_path
        self._cancel_event.clear()
        self._working = True
        self._set_controls(True)
        self._set_progress(0)
        self._set_status("Loading APEX neural engine and identity model...", False)
        threading.Thread(
            target=self._swap_worker,
            args=(source_path, video_path, filename),
            daemon=True,
        ).start()

    def cancel_swap(self, *_args) -> None:
        if self._working:
            self._cancel_event.set()
            self._set_status("Stopping safely...", False)

    def _swap_worker(self, source_path: str, video_path: str, filename: str) -> None:
        local_output = None
        try:
            from apex_engine import process_video
            local_output = os.path.join(_cache_dir(), "apex_" + filename)
            _delete_quietly(local_output)
            ok, result = process_video(
                source_path,
                video_path,
                local_output,
                progress_cb=self._engine_progress,
                cancel_cb=self._cancel_event.is_set,
            )
            if not ok:
                self._set_status(
                    "Face swap cancelled." if result == "Cancelled" else f"APEX failed: {result}",
                    result != "Cancelled",
                )
                return
            if not os.path.isfile(local_output) or os.path.getsize(local_output) <= 0:
                raise OSError("APEX reported success but produced no usable video")
            self._set_status("APEX complete. Publishing to Movies/FaceSwapPro...", False)
            saved_uri = _publish_video(local_output, filename)
            self._set_progress(100)
            self._set_status(
                f"COMPLETE\nSaved as {filename}\nMovies/FaceSwapPro\n{saved_uri}",
                False,
            )
        except Exception as exc:
            self._set_status(f"Unexpected APEX error: {type(exc).__name__}: {exc}", True)
        finally:
            _delete_quietly(local_output)
            self._finish_work()

    def _engine_progress(self, message: str, percent: int) -> None:
        self._set_progress(percent)
        self._set_status(message, False)

    @mainthread
    def _finish_work(self) -> None:
        self._working = False
        self._set_controls(False)

    @mainthread
    def _set_source_label(self, text: str, ok: bool) -> None:
        self.source_label.text = text
        self.source_label.color = (0.35, 1.0, 0.47, 1) if ok else (1.0, 0.58, 0.35, 1)

    @mainthread
    def _set_video_label(self, text: str, ok: bool) -> None:
        self.video_label.text = text
        self.video_label.color = (0.35, 1.0, 0.47, 1) if ok else (1.0, 0.58, 0.35, 1)

    @mainthread
    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.text = text
        self.status.color = (1.0, 0.48, 0.42, 1) if error else (0.75, 0.94, 0.78, 1)

    @mainthread
    def _set_progress(self, value: int) -> None:
        self.progress.value = max(0, min(100, int(value)))

    @mainthread
    def _set_controls(self, working: bool) -> None:
        for widget in (
            self.install_pro_button,
            self.install_ultra_button,
            self.source_button,
            self.video_button,
            self.run_button,
            self.output_name,
        ):
            widget.disabled = working
        self.cancel_button.disabled = not working

    def cleanup_cache_selections(self) -> None:
        _delete_quietly(self.source_image_path)
        _delete_quietly(self.target_video_path)
        self.source_image_path = None
        self.target_video_path = None
        try:
            from apex_ort import clear_sessions
            clear_sessions()
        except Exception:
            pass


class FaceSwapProApp(App):
    title = "FaceSwap Pro APEX"

    def build(self):
        return FaceSwapRoot()

    def on_stop(self):
        if isinstance(self.root, FaceSwapRoot):
            self.root.cleanup_cache_selections()


if __name__ == "__main__":
    FaceSwapProApp().run()
