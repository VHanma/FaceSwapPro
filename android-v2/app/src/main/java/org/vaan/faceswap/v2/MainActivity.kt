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
    var sources by remember { mutableStateOf<List<Uri>>(emptyList()) }
    var video by remember { mutableStateOf<Uri?>(null) }
    var mode by remember { mutableStateOf(QualityMode.BALANCED) }
    var status by remember { mutableStateOf("v2 native engine ready for setup") }
    var trackingBusy by remember { mutableStateOf(false) }

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
            Text("Add up to 8 source photos. Front, 3/4 and profile angles are designed to be scored and selected per target frame.")
            Button(onClick = { sourcePicker.launch(arrayOf("image/*")) }) {
                Text(if (sources.isEmpty()) "Choose identity photos" else "Identity photos: ${sources.size}")
            }

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
                    status = "Running real MediaPipe VIDEO-mode tracking probe…"
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
                    QualityMode.MOVIE -> "Maximum temporal, relighting, camera-match and bad-frame rerender path."
                }
            )

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
                    status = "Inputs ready. Pipeline: MediaPipe 478-point video tracking → identity vault → neural swap → semantic compositor → temporal/relight/restoration → quality gate."
                }
            ) { Text("Prepare v2 pipeline") }

            Card {
                Text(status, modifier = Modifier.padding(16.dp))
            }
        }
    }
}
