FROM python:3.11-slim

RUN python --version

WORKDIR /app

# System deps: gcc for asyncpg C ext, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# ── CPU-only PyTorch FIRST ───────────────────────────────────────────────────
# sentence-transformers pulls in torch as a dependency. The default PyPI
# wheel bundles CUDA libraries (~2-4GB) which are useless on Fargate (no
# GPU). Installing the CPU-only wheel from PyTorch's index first means pip
# finds it already satisfied later and skips the CUDA build entirely.
# Image size impact: ~1.5GB total vs ~4-5GB with the default CUDA wheel.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Pre-download the embedding model into the image ─────────────────────────
# Avoids a HuggingFace Hub network call (and associated cold-start latency
# or outage risk) every time a Fargate task starts cold.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='sentence-transformers/all-mpnet-base-v2', local_dir='/root/.cache/torch/sentence_transformers/sentence-transformers_all-mpnet-base-v2', local_dir_use_symlinks=False, ignore_patterns=['*.onnx', '*.onnx_data', 'onnx/*', 'openvino/*', 'openvino_model*', '*.bin', '*.xml'])"
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"


# App code
COPY alembic.ini .
COPY migrations/ ./migrations/
COPY app/ ./app/

# Non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
# Cached HF model lives in /root/.cache by default during the RUN above (root
# user); move ownership so appuser can read it too.
RUN mkdir -p /home/appuser/.cache && cp -r /root/.cache/* /home/appuser/.cache/ 2>/dev/null || true
RUN chown -R appuser:appuser /home/appuser/.cache
ENV HF_HOME=/home/appuser/.cache/huggingface
ENV SENTENCE_TRANSFORMERS_HOME=/home/appuser/.cache/torch/sentence_transformers

ENV TRANSFORMERS_CACHE=/app/cache
RUN mkdir -p /app/cache && chown -R appuser:appuser /app/cache

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
