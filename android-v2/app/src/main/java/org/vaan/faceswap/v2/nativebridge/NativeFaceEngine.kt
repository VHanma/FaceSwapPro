package org.vaan.faceswap.v2.nativebridge

object NativeFaceEngine {
    init {
        System.loadLibrary("faceswap_v2")
    }

    external fun selfTest(): String
    external fun createEngine(modelRoot: String, qualityMode: Int): Long
    external fun releaseEngine(handle: Long)
    external fun setIdentitySources(handle: Long, sourcePaths: Array<String>): Boolean
    external fun processRgbaFrame(
        handle: Long,
        rgba: java.nio.ByteBuffer,
        width: Int,
        height: Int,
        timestampUs: Long,
    ): Int
}
