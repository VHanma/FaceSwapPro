#!/usr/bin/env python3
"""FaceSwap Pro - Free on-device movie-grade face swap"""

import os
import threading

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.progressbar import ProgressBar
from kivy.clock import mainthread
from kivy.utils import platform
from kivy.metrics import dp
from kivy.core.window import Window

Window.clearcolor = (0.06, 0.06, 0.10, 1)


class Root(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(
            orientation='vertical',
            padding=[dp(16), dp(20), dp(16), dp(16)],
            spacing=dp(10),
            **kwargs
        )
        self.source_image_path = None
        self.target_video_path = None
        self.size_hint_y = None
        self.bind(minimum_height=self.setter('height'))
        self._build_ui()

    def _lbl(self, text, height=dp(30), **kw):
        return Label(
            text=text, size_hint_y=None, height=height,
            halign='left', text_size=(None, None), **kw
        )

    def _btn(self, text, color, height=dp(52)):
        b = Button(
            text=text, size_hint_y=None, height=height,
            background_color=color, font_size=dp(16)
        )
        return b

    def _build_ui(self):
        self.add_widget(Label(
            text='[b]FaceSwap Pro[/b]',
            markup=True, font_size=dp(30),
            size_hint_y=None, height=dp(55),
            color=(0.25, 0.85, 1, 1)
        ))
        self.add_widget(Label(
            text='100% FREE · On-device · Movie-grade',
            font_size=dp(13), size_hint_y=None, height=dp(22),
            color=(0.6, 0.6, 0.8, 1)
        ))
        self.add_widget(Label(size_hint_y=None, height=dp(6)))

        # Source face button
        self.source_btn = self._btn('📷  Pick Source Face Photo', (0.15, 0.55, 0.90, 1))
        self.source_btn.bind(on_press=self.pick_source)
        self.add_widget(self.source_btn)
        self.source_label = Label(
            text='No photo selected',
            size_hint_y=None, height=dp(24),
            color=(0.5, 0.5, 0.6, 1), font_size=dp(12)
        )
        self.add_widget(self.source_label)

        # Target video button
        self.video_btn = self._btn('🎬  Pick Target Video', (0.10, 0.65, 0.35, 1))
        self.video_btn.bind(on_press=self.pick_video)
        self.add_widget(self.video_btn)
        self.video_label = Label(
            text='No video selected',
            size_hint_y=None, height=dp(24),
            color=(0.5, 0.5, 0.6, 1), font_size=dp(12)
        )
        self.add_widget(self.video_label)

        # Output filename
        self.add_widget(Label(size_hint_y=None, height=dp(4)))
        self.add_widget(self._lbl('Output Filename (no extension):', color=(0.8, 0.8, 1, 1)))
        self.output_name = TextInput(
            text='my_faceswap',
            multiline=False, size_hint_y=None, height=dp(46),
            background_color=(0.12, 0.12, 0.18, 1),
            foreground_color=(1, 1, 1, 1)
        )
        self.add_widget(self.output_name)

        # Run button
        self.add_widget(Label(size_hint_y=None, height=dp(6)))
        self.run_btn = Button(
            text='🚀  SWAP FACE',
            size_hint_y=None, height=dp(64),
            background_color=(0.85, 0.18, 0.18, 1),
            font_size=dp(22), bold=True
        )
        self.run_btn.bind(on_press=self.run_faceswap)
        self.add_widget(self.run_btn)

        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(18))
        self.add_widget(self.progress)

        self.status = Label(
            text='Ready.\nPick a face photo and a target video, then tap Swap Face.',
            size_hint_y=None, height=dp(130),
            text_size=(dp(340), None),
            halign='left', valign='top',
            color=(0.75, 0.95, 0.75, 1),
            font_size=dp(13)
        )
        self.add_widget(self.status)

        self.save_label = Label(
            text='', size_hint_y=None, height=dp(50),
            color=(0.3, 1, 0.5, 1), font_size=dp(12),
            text_size=(dp(340), None), halign='left'
        )
        self.add_widget(self.save_label)

    # ── File pickers ─────────────────────────────────────────────────────────
    def pick_source(self, *args):
        if platform == 'android':
            try:
                from plyer import filechooser
                filechooser.open_file(
                    on_selection=self._on_source_select,
                    filters=['*.jpg', '*.jpeg', '*.png', '*.webp']
                )
            except Exception as e:
                self._update_status(f'Picker error: {e}')
        else:
            self.source_label.text = 'File picker (Android only in build)'

    def pick_video(self, *args):
        if platform == 'android':
            try:
                from plyer import filechooser
                filechooser.open_file(
                    on_selection=self._on_video_select,
                    filters=['*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm']
                )
            except Exception as e:
                self._update_status(f'Picker error: {e}')
        else:
            self.video_label.text = 'File picker (Android only in build)'

    def _on_source_select(self, selection):
        if selection:
            self.source_image_path = selection[0]
            self._set_source_label(f'✓ {os.path.basename(self.source_image_path)}', True)

    def _on_video_select(self, selection):
        if selection:
            self.target_video_path = selection[0]
            self._set_video_label(f'✓ {os.path.basename(self.target_video_path)}', True)

    @mainthread
    def _set_source_label(self, text, ok=False):
        self.source_label.text = text
        self.source_label.color = (0.3, 1, 0.3, 1) if ok else (1, 0.4, 0.4, 1)

    @mainthread
    def _set_video_label(self, text, ok=False):
        self.video_label.text = text
        self.video_label.color = (0.3, 1, 0.3, 1) if ok else (1, 0.4, 0.4, 1)

    # ── Main face swap logic ──────────────────────────────────────────────────
    def run_faceswap(self, *args):
        if not self.source_image_path:
            self._update_status('❌ Pick a source face photo first.')
            return
        if not self.target_video_path:
            self._update_status('❌ Pick a target video first.')
            return

        self.run_btn.disabled = True
        self._set_progress(0)
        self._update_status('🔄 Starting on-device face swap...')
        threading.Thread(target=self._faceswap_thread, daemon=True).start()

    def _faceswap_thread(self):
        try:
            from faceswap import process_video

            if platform == 'android':
                from android.storage import primary_external_storage_path
                save_dir = os.path.join(primary_external_storage_path(), 'Movies', 'FaceSwapPro')
            else:
                save_dir = os.path.join(os.path.expanduser('~'), 'FaceSwapPro')

            os.makedirs(save_dir, exist_ok=True)
            fname = (self.output_name.text.strip() or 'faceswap_result') + '.mp4'
            output_path = os.path.join(save_dir, fname)

            self._update_status('☁️ Sending to AI server...\nThis may take a few minutes.')

            def on_progress(message, pct):
                self._set_progress(pct)
                self._update_status(f'🧠 {message}\nPlease keep the app open...')

            ok, result = process_video(
                self.source_image_path,
                self.target_video_path,
                output_path,
                progress_cb=on_progress
            )

            if ok:
                # Notify Android gallery
                if platform == 'android':
                    try:
                        from jnius import autoclass
                        MSC = autoclass('android.media.MediaScannerConnection')
                        ctx = autoclass('org.kivy.android.PythonActivity').mActivity
                        MSC.scanFile(ctx, [output_path], None, None)
                    except Exception:
                        pass

                self._set_progress(100)
                self._update_status(f'✅ COMPLETE!\n{fname}\nSaved to Movies/FaceSwapPro/')
                self._set_save_label(output_path)
            else:
                self._update_status(f'❌ Failed: {result}')

        except Exception as e:
            self._update_status(f'❌ Error: {e}')
        finally:
            self._enable_btn()

    # ── Thread-safe UI helpers ────────────────────────────────────────────────
    @mainthread
    def _update_status(self, text):
        self.status.text = text

    @mainthread
    def _set_progress(self, val):
        self.progress.value = val

    @mainthread
    def _enable_btn(self):
        self.run_btn.disabled = False

    @mainthread
    def _set_save_label(self, path):
        self.save_label.text = f'📁 {path}'


class FaceSwapProApp(App):
    title = 'FaceSwap Pro'

    def build(self):
        if platform == 'android':
            from android.permissions import request_permissions, Permission
            request_permissions([
                Permission.READ_EXTERNAL_STORAGE,
                Permission.WRITE_EXTERNAL_STORAGE,
            ])

        sv = ScrollView(do_scroll_x=False)
        sv.add_widget(Root())
        return sv


if __name__ == '__main__':
    FaceSwapProApp().run()
