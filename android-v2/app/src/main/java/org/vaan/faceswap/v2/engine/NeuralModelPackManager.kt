package org.vaan.faceswap.v2.engine

import android.content.Context
import android.os.StatFs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Optional high-quality neural pack.
 *
 * These exact weights are not hidden inside the base APK: the pack is large and
 * its upstream models carry research/non-commercial usage terms. The user can
 * install it once, hashes are verified, and inference is offline afterward.
 */
class NeuralModelPackManager(private val context: Context) {

    data class ModelSpec(
        val fileName: String,
        val url: String,
        val sha256: String,
        val bytes: Long,
    )

    data class Progress(
        val model: String,
        val modelBytes: Long,
        val modelTotal: Long,
        val packBytes: Long,
        val packTotal: Long,
    )

    val directory: File = File(context.noBackupFilesDir, "movie-neural-pack")

    val movieResearchPack = listOf(
        ModelSpec(
            fileName = "arcface_w600k_r50.onnx",
            url = "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/arcface_w600k_r50.onnx",
            sha256 = "f1f79dc3b0b79a69f94799af1fffebff09fbd78fd96a275fd8f0cbbea23270d1",
            bytes = 174_388_474L,
        ),
        ModelSpec(
            fileName = "crossface_simswap.onnx",
            url = "https://github.com/facefusion/facefusion-assets/releases/download/models-3.4.0/crossface_simswap.onnx",
            sha256 = "6452a261ec30cc30afdbe4a426d82c3b10a476f2df794e3494071c02574e6829",
            bytes = 22_083_800L,
        ),
        ModelSpec(
            fileName = "simswap_unofficial_512.onnx",
            url = "https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/simswap_unofficial_512.onnx",
            sha256 = "fe805d1ce7d9e66322e2a8811f593a821e7d92f9ff861dd233794bdb2bb7a586",
            bytes = 239_249_034L,
        ),
    )

    val requiredBytes: Long get() = movieResearchPack.sumOf { it.bytes }

    fun availableBytes(): Long = StatFs(directory.parentFile?.absolutePath ?: context.filesDir.absolutePath).availableBytes

    suspend fun verifyInstalled(): Boolean = withContext(Dispatchers.IO) {
        movieResearchPack.all { spec ->
            val file = File(directory, spec.fileName)
            file.isFile && file.length() == spec.bytes && sha256(file).equals(spec.sha256, ignoreCase = true)
        }
    }

    suspend fun install(
        onProgress: (Progress) -> Unit = {},
    ): List<File> = withContext(Dispatchers.IO) {
        directory.mkdirs()
        val safetyMargin = 160L * 1024L * 1024L
        require(availableBytes() >= requiredBytes + safetyMargin) {
            "Movie Neural Pack needs about ${formatBytes(requiredBytes + safetyMargin)} free; available ${formatBytes(availableBytes())}"
        }

        var completeBeforeCurrent = 0L
        val installed = mutableListOf<File>()
        for (spec in movieResearchPack) {
            val destination = File(directory, spec.fileName)
            if (destination.isFile && destination.length() == spec.bytes && sha256(destination).equals(spec.sha256, true)) {
                completeBeforeCurrent += spec.bytes
                installed += destination
                onProgress(Progress(spec.fileName, spec.bytes, spec.bytes, completeBeforeCurrent, requiredBytes))
                continue
            }

            val temp = File(directory, spec.fileName + ".part")
            temp.delete()
            download(spec, temp, completeBeforeCurrent, onProgress)
            val actualHash = sha256(temp)
            require(actualHash.equals(spec.sha256, ignoreCase = true)) {
                temp.delete()
                "Hash mismatch for ${spec.fileName}: $actualHash"
            }
            require(temp.length() == spec.bytes) {
                temp.delete()
                "Unexpected size for ${spec.fileName}: ${temp.length()} instead of ${spec.bytes}"
            }
            if (destination.exists()) destination.delete()
            check(temp.renameTo(destination)) { "Could not finalize ${spec.fileName}" }
            completeBeforeCurrent += spec.bytes
            installed += destination
        }
        File(directory, "VERIFIED").writeText("sha256-verified\n")
        installed
    }

    fun file(name: String): File = File(directory, name)

    fun deletePack() {
        directory.deleteRecursively()
    }

    private fun download(
        spec: ModelSpec,
        destination: File,
        completeBeforeCurrent: Long,
        onProgress: (Progress) -> Unit,
    ) {
        val connection = (URL(spec.url).openConnection() as HttpURLConnection).apply {
            instanceFollowRedirects = true
            connectTimeout = 30_000
            readTimeout = 90_000
            requestMethod = "GET"
            setRequestProperty("User-Agent", "FaceSwapPro-v2")
        }
        try {
            connection.connect()
            require(connection.responseCode in 200..299) {
                "Download failed (${connection.responseCode}) for ${spec.fileName}"
            }
            connection.inputStream.use { input ->
                FileOutputStream(destination).use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    var modelBytes = 0L
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        modelBytes += read
                        onProgress(
                            Progress(
                                model = spec.fileName,
                                modelBytes = modelBytes,
                                modelTotal = spec.bytes,
                                packBytes = completeBeforeCurrent + modelBytes,
                                packTotal = requiredBytes,
                            )
                        )
                    }
                    output.fd.sync()
                }
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    companion object {
        fun formatBytes(bytes: Long): String {
            val mib = bytes / (1024.0 * 1024.0)
            return if (mib >= 1024.0) "%.2f GiB".format(mib / 1024.0) else "%.0f MiB".format(mib)
        }
    }
}
