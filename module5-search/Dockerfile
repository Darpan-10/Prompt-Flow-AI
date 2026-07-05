FROM python:3.11-slim

RUN python --version

WORKDIR /app

# System deps: curl for healthcheck (no gcc/libpq-dev needed -- asyncpg
# ships prebuilt wheels for this platform; Module 5 has no C-extension
# build requirements beyond what pip wheels already provide)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── CPU-only PyTorch FIRST ───────────────────────────────────────────────────
# Same rationale as Module 4: sentence-transformers depends on torch, and
# the default PyPI wheel bundles CUDA libraries (~2-4GB), useless on
# Fargate (no GPU). Installing the CPU-only wheel first means pip finds
# it already satisfied later and skips the CUDA download entirely.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Pre-download the embedding model into the image ─────────────────────────
# Same model as Module 4 (all-mpnet-base-v2) so query and document
# embeddings are dimensionally and semantically comparable.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-mpnet-base-v2')"

# App code
COPY app/ ./app/
COPY scripts/ ./scripts/

# Non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
RUN mkdir -p /home/appuser/.cache && cp -r /root/.cache/* /home/appuser/.cache/ 2>/dev/null || true
RUN chown -R appuser:appuser /home/appuser/.cache
ENV HF_HOME=/home/appuser/.cache/huggingface
ENV SENTENCE_TRANSFORMERS_HOME=/home/appuser/.cache/torch/sentence_transformers
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
