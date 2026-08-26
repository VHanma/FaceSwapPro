# Preserve JNI entry points and MediaPipe task classes used by reflection/native code.
-keep class org.vaan.faceswap.v2.nativebridge.** { *; }
-keep class com.google.mediapipe.** { *; }
-dontwarn com.google.mediapipe.**
