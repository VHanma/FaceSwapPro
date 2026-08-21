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
import androidx.compose.ui.unit.dp
import org.vaan.faceswap.v2.model.QualityMode
import org.vaan.faceswap.v2.nativebridge.NativeFaceEngine

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { FaceSwapV2Screen() } }
    }
}

@Composable
private fun FaceSwapV2Screen() {
    var sources by remember { mutableStateOf<List<Uri>>(emptyList()) }
    var video by remember { mutableStateOf<Uri?>(null) }
    var mode by remember { mutableStateOf(QualityMode.BALANCED) }
    var status by remember { mutableStateOf("v2 native engine ready for setup") }

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
            Text("Add up to 8 source photos. Front, 3/4 and profile angles will later be scored and selected per target frame.")
            Button(onClick = { sourcePicker.launch(arrayOf("image/*")) }) {
                Text(if (sources.isEmpty()) "Choose identity photos" else "Identity photos: ${sources.size}")
            }

            HorizontalDivider()
            Text("Target video", style = MaterialTheme.typography.headlineSmall)
            Button(onClick = { videoPicker.launch(arrayOf("video/*")) }) {
                Text(if (video == null) "Choose target video" else "Target video selected")
            }

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
                status = runCatching { NativeFaceEngine.selfTest() }
                    .fold(
                        onSuccess = { "Native core: $it" },
                        onFailure = { "Native core error: ${it.message}" }
                    )
            }) { Text("Run native engine self-test") }

            Button(
                enabled = sources.isNotEmpty() && video != null,
                onClick = {
                    status = "Inputs ready. Next pipeline stage: MediaPipe 478-point video tracking → identity vault → neural swap → semantic compositor."
                }
            ) { Text("Prepare v2 pipeline") }

            Card {
                Text(status, modifier = Modifier.padding(16.dp))
            }
        }
    }
}
