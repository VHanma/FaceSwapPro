cmake_minimum_required(VERSION 3.22.1)
project(faceswap_v2 LANGUAGES CXX)

add_library(faceswap_v2 SHARED native_bridge.cpp)

target_compile_features(faceswap_v2 PRIVATE cxx_std_20)
target_compile_options(faceswap_v2 PRIVATE -O3 -ffast-math -fvisibility=hidden)

find_library(log-lib log)
find_library(android-lib android)

target_link_libraries(faceswap_v2 PRIVATE ${log-lib} ${android-lib})

# v2 runtime slots:
# - ncnn/Vulkan is the primary planned neural backend.
# - ONNX Runtime is the compatibility backend.
# Their binary SDKs/model weights are intentionally not committed until
# model licenses and per-ABI packaging are locked down.
