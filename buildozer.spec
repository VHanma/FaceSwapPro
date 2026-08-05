[app]
title = FaceSwap Pro APEX
package.name = faceswappro
package.domain = org.vaan.faceswap
source.dir = .
source.include_exts = py,xml,png,jpg,jpeg,kv,atlas,json
source.exclude_dirs = .git,.github,.buildozer,bin,__pycache__,tests
version = 2.0.0

# Python remains the orchestration/UI layer. Heavy neural inference runs through
# the official ONNX Runtime Android AAR, while FFmpeg preserves H.264/AAC video.
requirements = python3,kivy,android,pyjnius,numpy,opencv,ffmpeg,av_codecs
orientation = portrait
fullscreen = 0

# APEX downloads model weights once from their published release assets. Source
# photos and target videos remain on-device and are never uploaded.
android.permissions = INTERNET
android.api = 36
android.minapi = 29
android.ndk = 28c
android.accept_sdk_license = True
android.archs = arm64-v8a
android.enable_androidx = True
android.private_storage = True
android.logcat_filters = *:S python:D

# Java bridge lets PyJNIus call the official Android ONNX Runtime efficiently.
android.add_src = java_src
android.gradle_dependencies = com.microsoft.onnxruntime:onnxruntime-android:1.27.0

# Modern p4a contains the FFmpeg executable packaging used by the proven v1.3.2
# video/audio path.
p4a.branch = v2026.05.09
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
