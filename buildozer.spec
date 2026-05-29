[app]
title = FaceSwap Pro
package.name = faceswappro
package.domain = org.vaan.faceswap
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
version = 1.0

requirements = python3,kivy==2.3.0,requests,certifi,charset-normalizer,urllib3,idna,plyer

orientation = portrait
fullscreen = 0

android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_IMAGES,READ_MEDIA_VIDEO
android.api = 33
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True

android.archs = arm64-v8a

# Use kivy bootstrap
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 1
