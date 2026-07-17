[app]
title = FaceSwap Pro
package.name = faceswappro
package.domain = org.vaan.faceswap
source.dir = .
source.include_exts = py,xml,png,jpg,jpeg,kv,atlas,json
source.exclude_dirs = .git,.github,.buildozer,bin,__pycache__,tests,recipes
version = 1.3.5

requirements = python3,kivy,android,pyjnius,numpy,opencv,ffpyplayer,ffpyplayer_codecs
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

p4a.bootstrap = sdl2
p4a.local_recipes = recipes

[buildozer]
log_level = 2
warn_on_root = 1
