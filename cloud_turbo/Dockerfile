FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    FACEFUSION_DIR=/opt/facefusion \
    TURBO_WORK_DIR=/tmp/faceswappro-turbo \
    ORT_TENSORRT_ENGINE_CACHE_ENABLE=1 \
    ORT_TENSORRT_CACHE_PATH=/opt/trt-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3-pip python3.12-venv git git-lfs ffmpeg curl ca-certificates \
    build-essential && rm -rf /var/lib/apt/lists/*

RUN python3.12 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --upgrade pip setuptools wheel

# Pin FaceFusion so a future upstream change cannot silently alter production.
RUN git clone https://github.com/facefusion/facefusion.git /opt/facefusion \
    && cd /opt/facefusion \
    && git checkout 358f169e95e2b02431722cc287db8acda6658df1 \
    && python install.py cuda@12 \
    && pip install tensorrt==10.12.0.36 --extra-index-url https://pypi.nvidia.com

COPY requirements.txt /opt/turbo/requirements.txt
RUN pip install -r /opt/turbo/requirements.txt
COPY server.py /opt/turbo/server.py

# Bake FaceFusion's model assets into the image so paid GPU time is spent on
# inference instead of downloading weights. TensorRT engines are cached on the
# first GPU start because engine plans are GPU-specific.
RUN cd /opt/facefusion && python facefusion.py force-download --download-scope full || true
RUN mkdir -p /opt/trt-cache /tmp/faceswappro-turbo

EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "server:app", "--app-dir", "/opt/turbo", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
