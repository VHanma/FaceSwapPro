#!/usr/bin/env python3
"""FaceSwap Pro Android UI. Fully offline, no account and no API key."""

from __future__ import annotations

import os
import re
import tempfile
import threading
from typing import Optional

from kivy.app import App
from kivy.clock import mainthread
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform

Window.clearcolor = (0.035, 0.04, 0.065, 1)

REQUEST_SOURCE = 4101
REQUEST_VIDEO = 4102


def _android_activity():
    from jnius import autoclass

    return autoclass("org.kivy.android.PythonActivity").mActivity


def _cache_dir() -> str:
    if platform == "android":
        return str(_android_activity().getCacheDir().getAbsolutePath())
    return tempfile.gettempdir()


def _copy_content_uri(uri, suffix: str) -> str:
    """Copy a Storage Access Framework URI into the app cache."""
    resolver = _android_activity().getContentResolver()
    input_stream = resolver.openInputStream(uri)
    if input_stream is None:
        raise OSError("Android could not open the selected file")

    fd, destination = tempfile.mkstemp(prefix="faceswappro_", suffix=suffix, dir=_cache_dir())
    os.close(fd)
    buffer = bytearray(1024 * 1024)
    try:
        with open(destination, "wb") as output:
            while True:
                count = input_stream.read(buffer)
                if count is None or int(count) < 0:
                    break
                if int(count) == 0:
                    continue
                output.write(buffer[: int(count)])
    finally:
        input_stream.close()
    return destination


def _publish_video(local_path: str, display_name: str) -> str:
    """Copy an MP4 into Movies/FaceSwapPro through Android MediaStore."""
    if platform != "android":
        return local_path

    from jnius import autoclass

    activity = _android_activity()
    resolver = activity.getContentResolver()
    ContentValues = autoclass("android.content.ContentValues")
    MediaStoreVideo = autoclass("android.provider.MediaStore$Video$Media")
    MediaColumns = autoclass("android.provider.MediaStore$MediaColumns")
    Environment = autoclass("android.os.Environment")

    values = ContentValues()
    values.put(MediaColumns.DISPLAY_NAME, display_name)
    values.put(MediaColumns.MIME_TYPE, "video/mp4")
    values.put(
        MediaColumns.RELATIVE_PATH,
        Environment.DIRECTORY_MOVIES + "/FaceSwapPro",
    )
    values.put(MediaColumns.IS_PENDING, 1)

    uri = resolver.insert(MediaStoreVideo.EXTERNAL_CONTENT_URI, values)
    if uri is None:
        raise OSError("Android could not create the result in Movies")

    output_stream = resolver.openOutputStream(uri)
    if output_stream is None:
        resolver.delete(uri, None, None)
        raise OSError("Android could not open the result file")

    try:
        with open(local_path, "rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output_stream.write(bytearray(chunk))
        output_stream.flush()
    except Exception:
        resolver.delete(uri, None, None)
        raise
    finally:
        output_stream.close()

    ready = ContentValues()
    ready.put(MediaColumns.IS_PENDING, 0)
    resolver.update(uri, ready, None, None)
    return str(uri.toString())


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
            padding=[dp(18), dp(20), dp(18), dp(30)],
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
        return Label(
            text=text,
            size_hint_y=None,
            height=height,
            text_size=(Window.width - dp(44), None),
            halign="left",
            valign="middle",
            **kwargs,
        )

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

    def _build_ui(self) -> None:
        self.content.add_widget(
            Label(
                text="[b]FaceSwap Pro[/b]",
                markup=True,
                size_hint_y=None,
                height=dp(58),
                font_size=dp(31),
                color=(0.30, 0.86, 1.0, 1),
            )
        )
        self.content.add_widget(
            self._fixed_label(
                "Offline face swapping on your phone. No server, login, token, or upload.",
                dp(52),
                font_size=dp(13),
                color=(0.72, 0.76, 0.88, 1),
            )
        )

        self.source_button = self._button("1. Choose source face photo", (0.10, 0.48, 0.86, 1))
        self.source_button.bind(on_release=self.choose_source)
        self.content.add_widget(self.source_button)
        self.source_label = self._fixed_label(
            "No source photo selected", dp(30), font_size=dp(12), color=(0.55, 0.58, 0.68, 1)
        )
        self.content.add_widget(self.source_label)

        self.video_button = self._button("2. Choose target video", (0.08, 0.62, 0.39, 1))
        self.video_button.bind(on_release=self.choose_video)
        self.content.add_widget(self.video_button)
        self.video_label = self._fixed_label(
            "No target video selected", dp(30), font_size=dp(12), color=(0.55, 0.58, 0.68, 1)
        )
        self.content.add_widget(self.video_label)

        self.content.add_widget(
            self._fixed_label(
                "Result filename", dp(30), font_size=dp(13), color=(0.84, 0.87, 0.98, 1)
            )
        )
        self.output_name = TextInput(
            text="my_faceswap",
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

        self.run_button = self._button("START FACE SWAP", (0.82, 0.16, 0.20, 1), dp(66))
        self.run_button.font_size = dp(21)
        self.run_button.bind(on_release=self.start_swap)
        self.content.add_widget(self.run_button)

        self.cancel_button = self._button("Cancel", (0.30, 0.31, 0.38, 1), dp(48))
        self.cancel_button.disabled = True
        self.cancel_button.bind(on_release=self.cancel_swap)
        self.content.add_widget(self.cancel_button)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(18))
        self.content.add_widget(self.progress)
        self.status = self._fixed_label(
            "Ready. Clear, front-facing source photos give the strongest result.",
            dp(130),
            font_size=dp(13),
            color=(0.75, 0.94, 0.78, 1),
        )
        self.status.valign = "top"
        self.content.add_widget(self.status)
        self.content.add_widget(
            self._fixed_label(
                "Results are saved to Movies/FaceSwapPro. Processing speed depends on video length and resolution. Output currently has video only.",
                dp(90),
                font_size=dp(12),
                color=(0.60, 0.64, 0.75, 1),
            )
        )

    def _open_picker(self, mime_type: str, request_code: int) -> None:
        if platform != "android":
            self._set_status("File selection is enabled in the Android APK.", error=True)
            return
        from jnius import autoclass

        Intent = autoclass("android.content.Intent")
        intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        intent.addCategory(Intent.CATEGORY_OPENABLE)
        intent.setType(mime_type)
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
            self._set_source_label("Loading source photo...", ok=False)
            threading.Thread(
                target=self._load_selection,
                args=(uri, ".jpg", "source"),
                daemon=True,
            ).start()
        elif int(request_code) == REQUEST_VIDEO:
            self._set_video_label("Loading target video...", ok=False)
            threading.Thread(
                target=self._load_selection,
                args=(uri, ".mp4", "video"),
                daemon=True,
            ).start()

    def _load_selection(self, uri, suffix: str, kind: str) -> None:
        try:
            path = _copy_content_uri(uri, suffix)
            if kind == "source":
                self.source_image_path = path
                self._set_source_label("Source photo ready", ok=True)
            else:
                self.target_video_path = path
                megabytes = os.path.getsize(path) / (1024 * 1024)
                self._set_video_label(f"Target video ready ({megabytes:.1f} MB)", ok=True)
        except Exception as exc:
            if kind == "source":
                self._set_source_label(f"Could not load photo: {exc}", ok=False)
            else:
                self._set_video_label(f"Could not load video: {exc}", ok=False)

    @staticmethod
    def _safe_filename(text: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip()).strip("._")
        return (cleaned or "faceswap_result")[:80] + ".mp4"

    def start_swap(self, *_args) -> None:
        if self._working:
            return
        if not self.source_image_path:
            self._set_status("Choose a source face photo first.", error=True)
            return
        if not self.target_video_path:
            self._set_status("Choose a target video first.", error=True)
            return

        self._cancel_event.clear()
        self._working = True
        self._set_controls(working=True)
        self._set_progress(0)
        self._set_status("Loading the offline face engine...", error=False)
        threading.Thread(target=self._swap_worker, daemon=True).start()

    def cancel_swap(self, *_args) -> None:
        if self._working:
            self._cancel_event.set()
            self._set_status("Stopping after the current frame...", error=False)

    def _swap_worker(self) -> None:
        local_output = None
        try:
            from faceswap import process_video

            filename = self._safe_filename(self.output_name.text)
            local_output = os.path.join(_cache_dir(), "result_" + filename)
            try:
                os.remove(local_output)
            except OSError:
                pass

            ok, result = process_video(
                self.source_image_path,
                self.target_video_path,
                local_output,
                progress_cb=self._engine_progress,
                cancel_cb=self._cancel_event.is_set,
            )
            if not ok:
                if result == "Cancelled":
                    self._set_status("Face swap cancelled.", error=False)
                else:
                    self._set_status(f"Face swap failed: {result}", error=True)
                return

            self._set_status("Processing finished. Saving to Movies/FaceSwapPro...", error=False)
            saved_uri = _publish_video(local_output, filename)
            self._set_progress(100)
            self._set_status(
                f"COMPLETE\nSaved as {filename}\nMovies/FaceSwapPro\n{saved_uri}",
                error=False,
            )
        except Exception as exc:
            self._set_status(f"Unexpected error: {type(exc).__name__}: {exc}", error=True)
        finally:
            if local_output:
                try:
                    os.remove(local_output)
                except OSError:
                    pass
            self._working = False
            self._set_controls(working=False)

    def _engine_progress(self, message: str, percent: int) -> None:
        self._set_progress(percent)
        self._set_status(message, error=False)

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
        self.source_button.disabled = working
        self.video_button.disabled = working
        self.run_button.disabled = working
        self.output_name.disabled = working
        self.cancel_button.disabled = not working


class FaceSwapProApp(App):
    title = "FaceSwap Pro"

    def build(self):
        return FaceSwapRoot()


if __name__ == "__main__":
    FaceSwapProApp().run()
