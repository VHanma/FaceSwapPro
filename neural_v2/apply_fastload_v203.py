#!/usr/bin/env python3
"""FaceSwapPro Neural v2.0.3 fast-start rescue.

Runs after the Omega + video performance patches. This patch targets the two
remaining phone bottlenecks:
1) app launch was gated by loading/downloading the full ~740 MB model stack;
2) two heavyweight FP32 models were unnecessary for the mobile path.

Changes:
- UI opens immediately; neural sessions load only when processing is requested.
- ArcFace R50 (~166 MB) -> MobileFaceNet w600k_mbf (~13.6 MB), still 512-D.
- INSwapper FP32 (~554 MB) -> fp16 (~278 MB).
- Legacy heavyweight cached models are deleted only after the new stack loads.
- Retry/backoff is shortened so a bad mirror fails over instead of looking hung.
- v2.0.2 fast video profile remains intact.
"""
from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('neural-upstream').resolve()
JAVA = ROOT / 'app/src/main/java/com/pv/androidfacefusion'


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'Fast-load patch anchor missing in {path}: {old[:120]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


# Version bump.
gradle = ROOT / 'app/build.gradle.kts'
replace_once(gradle, 'versionCode = 20003', 'versionCode = 20004')
replace_once(gradle, 'versionName = "2.0.2-alpha-video-fast"', 'versionName = "2.0.3-alpha-fastload"')

# ---------------------------------------------------------------------------
# Lightweight model pack.
# Keep SCRFD 10G for now because detector quality matters and it is only ~16 MB.
# Replace the two heavyweight models that dominate startup/storage.
# ---------------------------------------------------------------------------
downloader = JAVA / 'ModelDownloader.java'
s = downloader.read_text(encoding='utf-8')

s = s.replace(
    '"https://huggingface.co/leonelhs/insightface/resolve/main/w600k_r50.onnx"',
    '"https://huggingface.co/deepghs/insightface/resolve/main/buffalo_s/w600k_mbf.onnx",\n'
    '        "https://huggingface.co/jungseoik/ai_genportrait/resolve/main/buffalo_sc/w600k_mbf.onnx"'
)
s = s.replace(
    '"https://huggingface.co/leonelhs/insightface/resolve/main/inswapper_128.onnx",\n'
    '        "https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx"',
    '"https://github.com/facefusion/facefusion-assets/releases/download/models-3.0.0/inswapper_128_fp16.onnx",\n'
    '        "https://huggingface.co/ModelsLab/inswapper/resolve/main/inswapper_128_fp16.onnx"'
)

# Logical cache names.
s = s.replace('case "w600k_r50.onnx": return 100 * 1024 * 1024L; // ~166 MB',
              'case "w600k_mbf.onnx": return 10 * 1024 * 1024L; // ~13.6 MB')
s = s.replace('case "inswapper_128.onnx": return 500 * 1024 * 1024L; // ~554 MB',
              'case "inswapper_128_fp16.onnx": return 250 * 1024 * 1024L; // ~278 MB')
s = s.replace('new File(context.getFilesDir(), "w600k_r50.onnx")',
              'new File(context.getFilesDir(), "w600k_mbf.onnx")')
s = s.replace('new File(context.getFilesDir(), "inswapper_128.onnx")',
              'new File(context.getFilesDir(), "inswapper_128_fp16.onnx")')
s = s.replace('case "w600k_r50.onnx": return REC_MODEL_URLS;',
              'case "w600k_mbf.onnx": return REC_MODEL_URLS;')
s = s.replace('case "inswapper_128.onnx": return SWAP_MODEL_URLS;',
              'case "inswapper_128_fp16.onnx": return SWAP_MODEL_URLS;')

# Fail over mirrors faster. 10 x progressive two-second sleeps could make a
# network failure look frozen for minutes.
s = s.replace('int maxRetries = 10;', 'int maxRetries = 4;')
s = s.replace('Thread.sleep(2000L * attempt); // Exponential backoff',
              'Thread.sleep(750L * attempt); // Short mobile failover backoff')

downloader.write_text(s, encoding='utf-8')

# Point model wrappers at the lighter filenames.
embedder = JAVA / 'FaceEmbedder.java'
replace_once(embedder, 'getModelFile("w600k_r50.onnx")', 'getModelFile("w600k_mbf.onnx")')
swapper = JAVA / 'FaceSwapper.java'
replace_once(swapper, 'getModelFile("inswapper_128.onnx")', 'getModelFile("inswapper_128_fp16.onnx")')

# ---------------------------------------------------------------------------
# Lazy startup. Do not put a several-hundred-MB model gate in onCreate().
# Users can open the UI, choose source/target/video immediately, then model
# loading starts only when they press a processing action.
# ---------------------------------------------------------------------------
main = JAVA / 'MainActivity.java'
text = main.read_text(encoding='utf-8')

old = '''        initViews();
        initModels();
        setupListeners();
        requestPermissions();
        loadSavedFaces();'''
new = '''        initViews();
        setupListeners();
        requestPermissions();
        loadSavedFaces();
        Toast.makeText(this, "Fast start ready • choose media first", Toast.LENGTH_SHORT).show();'''
if old not in text:
    raise SystemExit('MainActivity onCreate model-init anchor missing')
text = text.replace(old, new, 1)

field_anchor = '    private ExecutorService executorService;\n'
field_add = field_anchor + '    private Runnable pendingAfterModels;\n    private volatile boolean modelInitStarted = false;\n'
if field_anchor not in text:
    raise SystemExit('MainActivity executor field anchor missing')
text = text.replace(field_anchor, field_add, 1)

method_anchor = '    private void initModels() {\n'
method_add = r'''    private synchronized void runWhenModelsReady(Runnable action) {
        if (processor != null) {
            action.run();
            return;
        }
        pendingAfterModels = action;
        if (modelInitStarted) {
            Toast.makeText(this, "Neural engine is loading…", Toast.LENGTH_SHORT).show();
            return;
        }
        modelInitStarted = true;
        initModels();
    }

    private void cleanupLegacyHeavyModels() {
        // Only run after the replacement sessions are alive. This recovers
        // storage from v2.0.0-v2.0.2 without risking a half-migrated install.
        try {
            File oldEmbed = new File(getFilesDir(), "w600k_r50.onnx");
            File oldSwap = new File(getFilesDir(), "inswapper_128.onnx");
            if (oldEmbed.exists()) oldEmbed.delete();
            if (oldSwap.exists()) oldSwap.delete();
        } catch (Throwable t) {
            Log.w(TAG, "Legacy model cleanup skipped", t);
        }
    }

''' + method_anchor
if method_anchor not in text:
    raise SystemExit('MainActivity initModels anchor missing')
text = text.replace(method_anchor, method_add, 1)

text = text.replace('case "w600k_r50.onnx": return "Face Embedder";',
                    'case "w600k_mbf.onnx": return "Mobile Face Embedder";')
text = text.replace('case "inswapper_128.onnx": return "Face Swapper";',
                    'case "inswapper_128_fp16.onnx": return "FP16 Face Swapper";')
text = text.replace('Downloading Face Embedder (~166 MB)', 'Downloading Mobile Face Embedder (~14 MB)')
text = text.replace('Downloading Face Swapper (~553 MB)', 'Downloading FP16 Face Swapper (~278 MB)')
text = text.replace('At least 800MB free storage', 'At least 350MB free storage')

text = text.replace('btnProcess.setOnClickListener(v -> processFaceFusion());',
                    'btnProcess.setOnClickListener(v -> runWhenModelsReady(this::processFaceFusion));')
text = text.replace('btnLibraryProcess.setOnClickListener(v -> processLibraryFaceFusion());',
                    'btnLibraryProcess.setOnClickListener(v -> runWhenModelsReady(this::processLibraryFaceFusion));')

success_anchor = '''                processor = new FaceFusionProcessor(faceDetector, faceEmbedder, faceSwapper);
                NeuralCore.publish(faceDetector, faceEmbedder, faceSwapper, processor);

                runOnUiThread(() -> {'''
success_new = '''                processor = new FaceFusionProcessor(faceDetector, faceEmbedder, faceSwapper);
                NeuralCore.publish(faceDetector, faceEmbedder, faceSwapper, processor);
                cleanupLegacyHeavyModels();

                runOnUiThread(() -> {'''
if success_anchor not in text:
    raise SystemExit('MainActivity processor publish anchor missing')
text = text.replace(success_anchor, success_new, 1)

ui_success_anchor = '''                    if (libraryTargetBitmap != null) {
                        detectLibraryTargetFacesAsync(libraryTargetBitmap);
                    }
                });'''
ui_success_new = '''                    if (libraryTargetBitmap != null) {
                        detectLibraryTargetFacesAsync(libraryTargetBitmap);
                    }
                    modelInitStarted = false;
                    Runnable ready = pendingAfterModels;
                    pendingAfterModels = null;
                    if (ready != null) ready.run();
                });'''
if ui_success_anchor not in text:
    raise SystemExit('MainActivity init success tail anchor missing')
text = text.replace(ui_success_anchor, ui_success_new, 1)

# On failure, re-enable actions and allow a clean retry.
error_anchor = '''                runOnUiThread(() -> {
                    hideOverlay();
                    showError("Failed to load models.\\n\\n" +'''
error_new = '''                runOnUiThread(() -> {
                    modelInitStarted = false;
                    pendingAfterModels = null;
                    btnProcess.setEnabled(true);
                    btnLibraryProcess.setEnabled(true);
                    hideOverlay();
                    showError("Failed to load models.\\n\\n" +'''
if error_anchor not in text:
    raise SystemExit('MainActivity error anchor missing')
text = text.replace(error_anchor, error_new, 1)
main.write_text(text, encoding='utf-8')

# ---------------------------------------------------------------------------
# NeuralCore direct video initialization automatically follows the new model
# filenames via FaceDetector/FaceEmbedder/FaceSwapper. Nothing starts at app
# launch, and VideoSwapActivity only initializes when rendering actually begins.
# ---------------------------------------------------------------------------

# Static validation.
checks = {
    gradle: ['versionCode = 20004', 'versionName = "2.0.3-alpha-fastload"'],
    downloader: ['w600k_mbf.onnx', 'inswapper_128_fp16.onnx', 'maxRetries = 4'],
    embedder: ['getModelFile("w600k_mbf.onnx")'],
    swapper: ['getModelFile("inswapper_128_fp16.onnx")'],
    main: ['runWhenModelsReady(this::processFaceFusion)', 'Fast start ready', 'cleanupLegacyHeavyModels()'],
}
for path, needles in checks.items():
    body = path.read_text(encoding='utf-8')
    for needle in needles:
        if needle not in body:
            raise SystemExit(f'Missing fast-load guard in {path}: {needle}')

if '        initModels();\n        setupListeners();' in main.read_text(encoding='utf-8'):
    raise SystemExit('Blocking startup initModels call remains')

print('OMEGA_FASTLOAD_V203_OK')
print('startup=instant UI; model init=on first render')
print('model_pack≈16MB detector + 13.6MB embedder + 278MB fp16 swapper')
print('video=v2.0.2 single-pass fast profile retained')
