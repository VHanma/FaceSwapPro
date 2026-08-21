package org.vaan.faceswap.v2

import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.vaan.faceswap.v2.engine.DefaultIdentityVault
import org.vaan.faceswap.v2.engine.NeuralModelPackManager
import org.vaan.faceswap.v2.engine.NeuralSwapPreviewRunner
import org.vaan.faceswap.v2.engine.SemanticMaskAnalyzer
import org.vaan.faceswap.v2.engine.VideoTrackingAnalyzer
import org.vaan.faceswap.v2.model.QualityMode
import org.vaan.faceswap.v2.nativebridge.RuntimeSelector

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { FaceSwapV2Screen() } }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FaceSwapV2Screen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val pack = remember { NeuralModelPackManager(context.applicationContext) }

    var sources by remember { mutableStateOf<List<Uri>>(emptyList()) }
    var video by remember { mutableStateOf<Uri?>(null) }
    var mode by remember { mutableStateOf(QualityMode.BALANCED) }
    var status by remember { mutableStateOf("v2 native engine ready for setup") }
    var trackingBusy by remember { mutableStateOf(false) }
    var maskBusy by remember { mutableStateOf(false) }
    var vaultBusy by remember { mutableStateOf(false) }
    var packBusy by remember { mutableStateOf(false) }
    var previewBusy by remember { mutableStateOf(false) }
    var packInstalled by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        packInstalled = runCatching { pack.verifyInstalled() }.getOrDefault(false)
    }

    val sourcePicker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments()
    ) { uris -> sources = uris.take(8) }

    val videoPicker = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri -> video = uri }

    Scaffold(
        topBar = { TopAppBar(title = { Text("FaceSwap Pro v2") }) }
    ) { padding ->
        Column(
            modifier = Modifier
                .padding(padding)
                .padding(20.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Text("Identity Vault", style = MaterialTheme.typography.headlineSmall)
            Text("Add up to 8 source photos. Front, 3/4 and profile angles are analyzed, identity-encoded and selected per target frame.")
            Button(onClick = { sourcePicker.launch(arrayOf("image/*")) }) {
                Text(if (sources.isEmpty()) "Choose identity photos" else "Identity photos: ${sources.size}")
            }
            Button(
                enabled = sources.isNotEmpty() && !vaultBusy,
                onClick = {
                    vaultBusy = true
                    status = "Building pose-aware 512-D Identity Vault…"
                    scope.launch {
                        status = runCatching {
                            val refs = DefaultIdentityVault(context).build(sources)
                            val poses = refs.joinToString(" | ") {
                                "yaw ${it.yaw.toInt()}° / pitch ${it.pitch.toInt()}° / q ${(it.sharpness * 100).toInt()}%"
                            }
                            "IDENTITY VAULT PASS • ${refs.size} usable source(s) • $poses"
                        }.getOrElse { "Identity Vault error: ${it.message}" }
                        vaultBusy = false
                    }
                }
            ) { Text(if (vaultBusy) "Building Identity Vault…" else "Build Identity Vault") }

            Button(
                enabled = sources.isNotEmpty() && !maskBusy,
                onClick = {
                    val selected = sources.firstOrNull() ?: return@Button
                    maskBusy = true
                    status = "Running 19-region neural face parsing…"
                    scope.launch {
                        status = runCatching {
                            withContext(Dispatchers.Default) {
                                SemanticMaskAnalyzer(context).analyze(selected).toString()
                            }
                        }.getOrElse { "Semantic mask probe error: ${it.message}" }
                        maskBusy = false
                    }
                }
            ) { Text(if (maskBusy) "Analyzing mask…" else "Test semantic masking") }

            HorizontalDivider()
            Text("Target video", style = MaterialTheme.typography.headlineSmall)
            Button(onClick = { videoPicker.launch(arrayOf("video/*")) }) {
                Text(if (video == null) "Choose target video" else "Target video selected")
            }

            Button(
                enabled = video != null && !trackingBusy,
                onClick = {
                    val selected = video ?: return@Button
                    trackingBusy = true
                    status = "Running MediaPipe VIDEO-mode tracking probe…"
                    scope.launch {
                        status = runCatching {
                            withContext(Dispatchers.Default) {
                                VideoTrackingAnalyzer(context).analyze(selected).toString()
                            }
                        }.getOrElse { "Tracking probe error: ${it.message}" }
                        trackingBusy = false
                    }
                }
            ) { Text(if (trackingBusy) "Analyzing tracking…" else "Test face tracking") }

            HorizontalDivider()
            Text("Quality", style = MaterialTheme.typography.headlineSmall)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                QualityMode.entries.forEach { candidate ->
                    FilterChip(
                        selected = mode == candidate,
                        onClick = { mode = candidate },
                        label = { Text(candidate.label) }
                    )
                }
            }
            Text(
                when (mode) {
                    QualityMode.FAST -> "256px internal face render, minimal refinement."
                    QualityMode.BALANCED -> "512px render, semantic/temporal refinement path enabled."
                    QualityMode.MOVIE -> "512px neural identity synthesis, semantic compositing and maximum refinement path."
                }
            )

            HorizontalDivider()
            Text("Movie Neural Pack", style = MaterialTheme.typography.headlineSmall)
            Text(
                if (packInstalled) {
                    "Installed + SHA-256 verified. Neural synthesis runs offline after installation."
                } else {
                    "Optional ~416 MiB research/non-commercial model pack: ArcFace R50 + CrossFace + SimSwap 512."
                }
            )
            Button(
                enabled = !packBusy,
                onClick = {
                    if (packInstalled) {
                        status = "Movie Neural Pack is installed and verified."
                    } else {
                        packBusy = true
                        status = "Installing Movie Neural Pack…"
                        scope.launch {
                            status = runCatching {
                                pack.install { progress ->
                                    val percent = if (progress.packTotal <= 0L) 0 else
                                        (progress.packBytes * 100L / progress.packTotal).toInt().coerceIn(0, 100)
                                    scope.launch {
                                        status = "Movie Neural Pack $percent% • ${progress.model}"
                                    }
                                }
                                packInstalled = pack.verifyInstalled()
                                if (packInstalled) {
                                    "MOVIE PACK READY • ${NeuralModelPackManager.formatBytes(pack.requiredBytes)} verified"
                                } else {
                                    "Movie pack downloaded but verification did not pass"
                                }
                            }.getOrElse { "Movie pack error: ${it.message}" }
                            packBusy = false
                        }
                    }
                }
            ) { Text(if (packBusy) "Installing neural models…" else if (packInstalled) "Verify Movie Neural Pack" else "Install Movie Neural Pack") }

            Button(
                enabled = packInstalled && sources.isNotEmpty() && video != null && !previewBusy,
                onClick = {
                    val selectedVideo = video ?: return@Button
                    previewBusy = true
                    status = "Rendering real SimSwap-512 neural preview…"
                    scope.launch {
                        status = runCatching {
                            val result = NeuralSwapPreviewRunner(context).run(sources, selectedVideo, pack)
                            "NEURAL PREVIEW SAVED • source yaw ${result.sourceYaw.toInt()}° → target yaw ${result.targetYaw.toInt()}° / pitch ${result.targetPitch.toInt()}° • Pictures/FaceSwapPro"
                        }.getOrElse { "Neural preview error: ${it.message}" }
                        previewBusy = false
                    }
                }
            ) { Text(if (previewBusy) "Rendering neural preview…" else "Render Movie neural preview") }

            HorizontalDivider()
            Button(onClick = {
                status = runCatching { RuntimeSelector.report().toString() }
                    .fold(
                        onSuccess = { it },
                        onFailure = { "Runtime probe error: ${it.message}" }
                    )
            }) { Text("Probe AI acceleration") }

            Button(
                enabled = sources.isNotEmpty() && video != null,
                onClick = {
                    status = "Inputs ready. Pipeline: 478-point tracking → pose-aware identity selection → SimSwap 512 → 19-region compositor → temporal/relight/restoration → quality gate."
                }
            ) { Text("Prepare v2 pipeline") }

            Card {
                Text(status, modifier = Modifier.padding(16.dp))
            }
        }
    }
}
