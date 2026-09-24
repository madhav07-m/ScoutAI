# Cloud Run deployment image for the Scout AI backend.
#
# Run from the project root (same directory as requirements.txt, app/,
# backend/) — this is what `gcloud run deploy --source .` will build
# automatically since it detects this Dockerfile.

FROM python:3.11-slim

# System deps:
#   tesseract-ocr — pytesseract is just a Python wrapper; the actual
#     OCR engine has to be installed separately (see app/parsing.py's
#     OCR fallback for graphic/scanned resumes). Without this package,
#     OCR fallback silently no-ops instead of crashing, but you'd lose
#     that fallback entirely in production.
#   libgomp1 — onnxruntime (used by fastembed) needs OpenMP's runtime
#     library; python:3.11-slim doesn't include it by default and
#     onnxruntime fails to import without it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (separate layer) so `docker build` reuses
# this layer on rebuilds where only application code changed, not deps.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-bake the embedding model into the image at BUILD time, not
# runtime. Without this, every Cloud Run cold start (which happens
# often on the free/pay-per-use tier since instances scale to zero)
# would re-download the ~90MB MiniLM model from Hugging Face before it
# could serve a single request — this bakes it into an image layer
# instead, so cold starts don't pay that cost at all.
ENV FASTEMBED_CACHE_DIR=/app/.cache/fastembed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2', cache_dir='/app/.cache/fastembed')"

# Now copy the actual application code.
COPY app/ ./app/
COPY backend/ ./backend/

# Cloud Run injects the PORT env var (defaults to 8080) and requires
# the container to listen on 0.0.0.0:$PORT — hardcoding 8000 here
# would make the container fail Cloud Run's startup health check.
# Shell form (not exec-form array) is required so $PORT actually
# expands; exec form does not invoke a shell and would pass the
# literal string "$PORT" to uvicorn.
CMD exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8080}
