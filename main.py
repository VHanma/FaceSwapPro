#!/usr/bin/env python3
"""FaceSwap Pro - Movie-grade AI face swap for Android"""

import os
import json
import time
import threading
import base64
import requests

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock, mainthread
from kivy.storage.jsonstore import JsonStore
from kivy.utils import platform
from kivy.metrics import dp
from kivy.core.window import Window

REPLICATE_VERSION = "11b6bf0f4e14d808f655e87e5448233cceff10a45f659d71539cafb7163b2e84"

Window.clearcolor = (0.06, 0.06, 0.10, 1)


class Root(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(
            orientation='vertical',
            padding=[dp(16), dp(20), dp(16), dp(16)],
            spacing=dp(10),
            **kwargs
        )
        self.store = JsonStore('faceswap_config.json')
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
        # Title
        self.add_widget(Label(
            text='[b]FaceSwap Pro[/b]',
            markup=True, font_size=dp(30),
            size_hint_y=None, height=dp(55),
            color=(0.25, 0.85, 1, 1)
        ))
        self.add_widget(Label(
            text='Movie-grade AI face replacement',
            font_size=dp(13), size_hint_y=None, height=dp(22),
            color=(0.6, 0.6, 0.8, 1)
        ))

        # Divider
        self.add_widget(Label(size_hint_y=None, height=dp(6)))

        # API Key
        self.add_widget(self._lbl('Replicate API Key:', color=(0.8, 0.8, 1, 1)))
        saved_key = self.store.get('config')['api_key'] if self.store.exists('config') else ''
        self.api_key_input = TextInput(
            text=saved_key, password=True,
            hint_text='r8_xxxxxxxxxxxx',
            multiline=False, size_hint_y=None, height=dp(46),
            background_color=(0.12, 0.12, 0.18, 1),
            foreground_color=(1, 1, 1, 1)
        )
        self.add_widget(self.api_key_input)

        # Source face button
        self.add_widget(Label(size_hint_y=None, height=dp(4)))
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

        # Progress bar
        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(18))
        self.add_widget(self.progress)

        # Status log
        self.status = Label(
            text='Ready.\nPick a face photo and a target video, then tap Swap Face.',
            size_hint_y=None, height=dp(130),
            text_size=(dp(340), None),
            halign='left', valign='top',
            color=(0.75, 0.95, 0.75, 1),
            font_size=dp(13)
        )
        self.add_widget(self.status)

        # Save path info
        self.save_label = Label(
            text='', size_hint_y=None, height=dp(50),
            color=(0.3, 1, 0.5, 1), font_size=dp(12),
            text_size=(dp(340), None), halign='left'
        )
        self.add_widget(self.save_label)

    # ── File pickers ────────────────────────────────────────────────────────
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
            name = os.path.basename(self.source_image_path)
            self._set_source_label(f'✓ {name}', True)

    def _on_video_select(self, selection):
        if selection:
            self.target_video_path = selection[0]
            name = os.path.basename(self.target_video_path)
            self._set_video_label(f'✓ {name}', True)

    @mainthread
    def _set_source_label(self, text, ok=False):
        self.source_label.text = text
        self.source_label.color = (0.3, 1, 0.3, 1) if ok else (1, 0.4, 0.4, 1)

    @mainthread
    def _set_video_label(self, text, ok=False):
        self.video_label.text = text
        self.video_label.color = (0.3, 1, 0.3, 1) if ok else (1, 0.4, 0.4, 1)

    # ── Main face swap logic ─────────────────────────────────────────────────
    def run_faceswap(self, *args):
        api_key = self.api_key_input.text.strip()
        if not api_key:
            self._update_status('❌ Enter your Replicate API key first.')
            return
        if not self.source_image_path:
            self._update_status('❌ Pick a source face photo first.')
            return
        if not self.target_video_path:
            self._update_status('❌ Pick a target video first.')
            return

        self.store.put('config', api_key=api_key)
        self.run_btn.disabled = True
        self._set_progress(0)
        self._update_status('🔄 Starting...')
        threading.Thread(target=self._faceswap_thread, daemon=True).start()

    def _faceswap_thread(self):
        api_key = self.api_key_input.text.strip()
        auth_headers = {'Authorization': f'Bearer {api_key}'}
        json_headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        try:
            # STEP 1 — Upload source face image
            self._update_status('📤 [1/4] Uploading source face image...')
            self._set_progress(8)

            with open(self.source_image_path, 'rb') as f:
                img_bytes = f.read()

            ext = os.path.splitext(self.source_image_path)[1].lower()
            mime = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                    'png': 'image/png', 'webp': 'image/webp'}.get(ext.lstrip('.'), 'image/jpeg')

            img_up = requests.post(
                'https://api.replicate.com/v1/files',
                headers={**auth_headers, 'Content-Type': mime},
                data=img_bytes,
                timeout=120
            )
            if img_up.status_code not in (200, 201):
                self._update_status(f'❌ Face upload failed ({img_up.status_code}): {img_up.text[:200]}')
                self._enable_btn()
                return

            source_url = img_up.json()['urls']['get']
            self._update_status(f'✓ Face image uploaded.')
            self._set_progress(22)

            # STEP 2 — Upload target video
            vid_size_mb = os.path.getsize(self.target_video_path) / (1024 * 1024)
            self._update_status(f'📤 [2/4] Uploading video ({vid_size_mb:.1f} MB)...')

            with open(self.target_video_path, 'rb') as f:
                vid_bytes = f.read()

            vid_up = requests.post(
                'https://api.replicate.com/v1/files',
                headers={**auth_headers, 'Content-Type': 'video/mp4'},
                data=vid_bytes,
                timeout=600
            )
            if vid_up.status_code not in (200, 201):
                self._update_status(f'❌ Video upload failed ({vid_up.status_code}): {vid_up.text[:200]}')
                self._enable_btn()
                return

            target_url = vid_up.json()['urls']['get']
            self._update_status(f'✓ Video uploaded ({vid_size_mb:.1f} MB).')
            self._set_progress(45)

            # STEP 3 — Run prediction
            self._update_status('🧠 [3/4] Running AI face swap on A100 GPU...')
            pred = requests.post(
                'https://api.replicate.com/v1/predictions',
                headers=json_headers,
                json={
                    'version': REPLICATE_VERSION,
                    'input': {
                        'source_image': source_url,
                        'target_video': target_url
                    }
                },
                timeout=30
            )
            if pred.status_code not in (200, 201):
                self._update_status(f'❌ Prediction failed ({pred.status_code}): {pred.text[:200]}')
                self._enable_btn()
                return

            pred_id = pred.json()['id']
            poll_url = f'https://api.replicate.com/v1/predictions/{pred_id}'
            self._update_status(f'⏳ [3/4] Processing... (ID: {pred_id[:8]}…)')

            # STEP 4 — Poll
            elapsed = 0
            while elapsed < 600:
                time.sleep(5)
                elapsed += 5
                r = requests.get(poll_url, headers=auth_headers, timeout=30)
                data = r.json()
                s = data.get('status', '')
                p = min(45 + int(elapsed / 600 * 45), 90)
                self._set_progress(p)
                self._update_status(f'⏳ [3/4] Processing... {elapsed}s (status: {s})')

                if s == 'succeeded':
                    out = data.get('output')
                    video_url = out if isinstance(out, str) else (out[0] if out else None)
                    if not video_url:
                        self._update_status('❌ Model returned no output.')
                        self._enable_btn()
                        return
                    self._download_result(video_url)
                    return

                if s in ('failed', 'canceled'):
                    err = data.get('error', 'Unknown error')
                    self._update_status(f'❌ Model {s}: {err}')
                    self._enable_btn()
                    return

            self._update_status('❌ Timed out after 10 minutes.')
            self._enable_btn()

        except Exception as e:
            self._update_status(f'❌ Exception: {e}')
            self._enable_btn()

    def _download_result(self, url):
        try:
            self._update_status('📥 [4/4] Downloading result video...')
            resp = requests.get(url, stream=True, timeout=180)

            if platform == 'android':
                from android.storage import primary_external_storage_path
                save_dir = os.path.join(primary_external_storage_path(), 'Movies', 'FaceSwapPro')
            else:
                save_dir = os.path.join(os.path.expanduser('~'), 'FaceSwapPro')

            os.makedirs(save_dir, exist_ok=True)
            fname = (self.output_name.text.strip() or 'faceswap_result') + '.mp4'
            save_path = os.path.join(save_dir, fname)

            total = 0
            with open(save_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
                        self._update_status(f'📥 Downloading... {total // (1024*1024)} MB')

            # Tell Android gallery about the new file
            if platform == 'android':
                try:
                    from jnius import autoclass
                    MSC = autoclass('android.media.MediaScannerConnection')
                    ctx = autoclass('org.kivy.android.PythonActivity').mActivity
                    MSC.scanFile(ctx, [save_path], None, None)
                except Exception:
                    pass

            self._set_progress(100)
            self._update_status(f'✅ COMPLETE!\n{fname}\nSaved to Movies/FaceSwapPro/')
            self._set_save_label(save_path)

        except Exception as e:
            self._update_status(f'❌ Download error: {e}')
        finally:
            self._enable_btn()

    # ── Thread-safe UI helpers ───────────────────────────────────────────────
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
                Permission.INTERNET,
            ])

        sv = ScrollView(do_scroll_x=False)
        root = Root()
        sv.add_widget(root)
        return sv


if __name__ == '__main__':
    FaceSwapProApp().run()
