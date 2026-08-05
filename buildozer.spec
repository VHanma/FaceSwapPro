[app]
title = FaceSwap Pro
package.name = faceswappro
package.domain = org.vaan.faceswap
source.dir = .
source.include_exts = py,xml,png,jpg,jpeg,kv,atlas,json
source.exclude_dirs = .git,.github,.buildozer,bin,__pycache__,tests
version = 1.3.2

# FFmpeg + av_codecs gives the Android build a real H.264/AAC encoder instead
# of depending on OpenCV VideoWriter codec availability.
requirements = python3,kivy,android,pyjnius,numpy,opencv,ffmpeg,av_codecs
orientation = portrait
fullscreen = 0

# Storage Access Framework picks files. MediaStore saves into Movies/FaceSwapPro.
# No broad file-access or internet permission is required.
android.api = 36
android.minapi = 29
android.ndk = 28c
android.accept_sdk_license = True
android.archs = arm64-v8a
android.enable_androidx = True
android.private_storage = True
android.logcat_filters = *:S python:D

# Pin the modern p4a release that exposes the bundled ffmpeg executable on
# Android and supports encoder-enabled FFmpeg builds.
p4a.branch = v2026.05.09
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
