import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import gradio as gr
import spaces
from huggingface_hub import hf_hub_download

ROOT = Path('/tmp/faceswappro-free-turbo')
SIMSWAP = ROOT / 'SimSwap'
BOOT_LOCK = threading.Lock()
READY = False
WORKER_SECRET = os.environ.get('FSP_WORKER_SECRET', '')
SIMSWAP_COMMIT = 'bd7b7686a17f41dd11cfcd5d82f7e4c5eb94b780'


def _link_or_copy(src: str, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.symlink(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def _patch(path: Path, old: str, new: str):
    text = path.read_text(encoding='utf-8')
    if old in text:
        path.write_text(text.replace(old, new), encoding='utf-8')


def prepare_runtime():
    global READY
    if READY:
        return
    with BOOT_LOCK:
        if READY:
            return
        ROOT.mkdir(parents=True, exist_ok=True)
        if not SIMSWAP.exists():
            subprocess.run([
                'git', 'clone', '--filter=blob:none', 'https://github.com/neuralchen/SimSwap.git', str(SIMSWAP)
            ], check=True)
            subprocess.run(['git', '-C', str(SIMSWAP), 'checkout', SIMSWAP_COMMIT], check=True)

        # Python 3.10+/modern PyTorch compatibility patches.
        _patch(SIMSWAP / 'test_video_swapsingle.py', 'import fractions', 'import math')
        _patch(SIMSWAP / 'test_video_swapsingle.py', 'fractions.gcd', 'math.gcd')
        _patch(SIMSWAP / 'test_video_swapsingle.py', 'app.prepare(ctx_id= 0', 'app.prepare(ctx_id= -1')
        _patch(
            SIMSWAP / 'models/fs_model.py',
            'torch.load(netArc_checkpoint, map_location=torch.device("cpu"))',
            'torch.load(netArc_checkpoint, map_location=torch.device("cpu"), weights_only=False)'
        )

        generator = hf_hub_download('netrunner-exe/SimSwap-models', 'simswap_512_beta.pth')
        arcface = hf_hub_download('netrunner-exe/SimSwap-models', 'arcface_checkpoint.tar')
        parser = hf_hub_download('leonelhs/faceparser', '79999_iter.pth')
        detector = hf_hub_download('kidyu/antelopev2-for-InstantID-ComfyUI', 'scrfd_10g_bnkps.onnx')

        _link_or_copy(generator, SIMSWAP / 'checkpoints/512/550000_net_G.pth')
        _link_or_copy(arcface, SIMSWAP / 'arcface_model/arcface_checkpoint.tar')
        _link_or_copy(parser, SIMSWAP / 'parsing_model/checkpoint/79999_iter.pth')
        _link_or_copy(detector, SIMSWAP / 'insightface_func/models/antelope/scrfd_10g_bnkps.onnx')
        READY = True


def _validate_secret(secret: str):
    if WORKER_SECRET and secret != WORKER_SECRET:
        raise gr.Error('Free Turbo authorization failed')


@spaces.GPU(duration=300)
def swap_video(source_path: str, video_path: str, secret: str, quality: str = 'Ultra 512'):
    _validate_secret(secret)
    if not source_path or not video_path:
        raise gr.Error('Choose a source face and a video')
    prepare_runtime()

    job = Path(tempfile.mkdtemp(prefix='fsp_', dir='/tmp'))
    output = job / 'FaceSwapPro_FreeTurbo.mp4'
    temp_frames = job / 'frames'
    crop = '512' if '512' in quality else '224'

    cmd = [
        'python', 'test_video_swapsingle.py',
        '--crop_size', crop,
        '--use_mask',
        '--no_simswaplogo',
        '--name', '512' if crop == '512' else 'people',
        '--Arc_path', 'arcface_model/arcface_checkpoint.tar',
        '--pic_a_path', str(source_path),
        '--video_path', str(video_path),
        '--output_path', str(output),
        '--temp_path', str(temp_frames),
    ]
    env = os.environ.copy()
    env['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'
    proc = subprocess.run(
        cmd,
        cwd=SIMSWAP,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=290,
    )
    if proc.returncode != 0 or not output.exists() or output.stat().st_size < 1024:
        tail = (proc.stdout or '')[-5000:]
        raise gr.Error('Free Turbo render failed: ' + tail[-1500:])
    return str(output)


# Warm only CPU/download state. No GPU is allocated here and no user quota is consumed.
try:
    prepare_runtime()
except Exception as exc:
    print('Deferred runtime preparation:', exc, flush=True)

with gr.Blocks(title='FaceSwapPro Free Turbo') as demo:
    gr.Markdown('## FaceSwapPro Free Turbo\nPrivate app gate + Hugging Face ZeroGPU. Face media is used only for the requested render.')
    with gr.Row():
        source = gr.Image(type='filepath', label='Source face')
        target = gr.Video(label='Target video')
    secret = gr.Textbox(type='password', visible=False)
    quality = gr.Radio(['Ultra 512', 'Fast 224'], value='Ultra 512', label='Quality')
    run = gr.Button('Render')
    output = gr.Video(label='Result')
    run.click(swap_video, [source, target, secret, quality], output, api_name='swap_video')

demo.queue(default_concurrency_limit=1).launch(max_file_size='1500mb')
