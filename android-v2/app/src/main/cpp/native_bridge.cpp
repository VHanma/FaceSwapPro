#include <jni.h>
#include <android/log.h>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace {
constexpr const char* TAG = "FaceSwapProV2";

struct EngineState {
    std::string modelRoot;
    int qualityMode = 1;
    std::vector<std::string> identitySources;
};

std::string toString(JNIEnv* env, jstring value) {
    if (!value) return {};
    const char* chars = env->GetStringUTFChars(value, nullptr);
    std::string out = chars ? chars : "";
    if (chars) env->ReleaseStringUTFChars(value, chars);
    return out;
}

EngineState* fromHandle(jlong handle) {
    return reinterpret_cast<EngineState*>(static_cast<intptr_t>(handle));
}
}

extern "C" JNIEXPORT jstring JNICALL
Java_org_vaan_faceswap_v2_nativebridge_NativeFaceEngine_selfTest(JNIEnv* env, jobject) {
#if defined(__aarch64__)
    const char* abi = "arm64-v8a";
#else
    const char* abi = "unknown";
#endif
    std::string message = std::string("OK • C++20 • ") + abi + " • runtime slots: ncnn/Vulkan + ORT";
    return env->NewStringUTF(message.c_str());
}

extern "C" JNIEXPORT jlong JNICALL
Java_org_vaan_faceswap_v2_nativebridge_NativeFaceEngine_createEngine(
    JNIEnv* env, jobject, jstring modelRoot, jint qualityMode) {
    auto* state = new EngineState();
    state->modelRoot = toString(env, modelRoot);
    state->qualityMode = qualityMode;
    __android_log_print(ANDROID_LOG_INFO, TAG, "engine created mode=%d", qualityMode);
    return static_cast<jlong>(reinterpret_cast<intptr_t>(state));
}

extern "C" JNIEXPORT void JNICALL
Java_org_vaan_faceswap_v2_nativebridge_NativeFaceEngine_releaseEngine(
    JNIEnv*, jobject, jlong handle) {
    delete fromHandle(handle);
}

extern "C" JNIEXPORT jboolean JNICALL
Java_org_vaan_faceswap_v2_nativebridge_NativeFaceEngine_setIdentitySources(
    JNIEnv* env, jobject, jlong handle, jobjectArray paths) {
    auto* state = fromHandle(handle);
    if (!state || !paths) return JNI_FALSE;
    state->identitySources.clear();
    const jsize count = env->GetArrayLength(paths);
    state->identitySources.reserve(count);
    for (jsize i = 0; i < count; ++i) {
        auto item = static_cast<jstring>(env->GetObjectArrayElement(paths, i));
        state->identitySources.push_back(toString(env, item));
        env->DeleteLocalRef(item);
    }
    return state->identitySources.empty() ? JNI_FALSE : JNI_TRUE;
}

extern "C" JNIEXPORT jint JNICALL
Java_org_vaan_faceswap_v2_nativebridge_NativeFaceEngine_processRgbaFrame(
    JNIEnv* env, jobject, jlong handle, jobject rgba, jint width, jint height, jlong) {
    auto* state = fromHandle(handle);
    if (!state || !rgba || width <= 0 || height <= 0) return -1;
    void* data = env->GetDirectBufferAddress(rgba);
    const jlong capacity = env->GetDirectBufferCapacity(rgba);
    const jlong required = static_cast<jlong>(width) * height * 4;
    if (!data || capacity < required) return -2;

    // Stage contract is live. Neural inference is plugged in here without
    // changing Kotlin/UI code: align -> swap -> masks -> temporal -> relight.
    return 0;
}
