package org.vaan.faceswap.v2.nativebridge

import ai.onnxruntime.OrtEnvironment

object RuntimeSelector {
    data class RuntimeReport(
        val ortVersion: String,
        val providers: List<String>,
        val nativeCore: String,
    ) {
        override fun toString(): String =
            "Native=$nativeCore | ORT=$ortVersion | Providers=${providers.joinToString()}"
    }

    fun report(): RuntimeReport {
        val env = OrtEnvironment.getEnvironment()
        val providers = OrtEnvironment.getAvailableProviders().map { it.name }
        return RuntimeReport(
            ortVersion = env.version,
            providers = providers,
            nativeCore = NativeFaceEngine.selfTest(),
        )
    }
}
