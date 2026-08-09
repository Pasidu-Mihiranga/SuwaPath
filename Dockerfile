# SuwaPath API.
#
# The frontend is not built here — it is a static SPA deployed separately to a
# CDN, so this image carries only the API and the models it runs. That keeps
# the container to one job and means a UI change does not rebuild an 800 MB
# Python image.
#
# Two decisions worth knowing:
#
# **Debian slim, not Alpine.** onnxruntime, tokenizers and grpcio publish
# manylinux wheels but not musl ones, so Alpine would compile them from source
# and turn a six-minute build into an hour.
#
# **The embedding model is baked in.** Left to itself, fastembed downloads
# ~90 MB on first use. On a platform that rebuilds containers that download
# happens on every cold start, inside the first request. Fetching it at build
# time makes cold starts predictable.

# --------------------------------------------------------------------- deps
FROM python:3.12-slim-bookworm AS deps

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential is insurance for any package that falls back to an sdist on
# this architecture. It is discarded with this stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# ------------------------------------------------------------------ runtime
FROM python:3.12-slim-bookworm AS runtime

# tesseract is required, not optional: the OCR pipeline shells out to it for
# scanned documents that carry no text layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# UID 1000 matches what most container platforms run as. Anything owned by
# root here would be unwritable at runtime.
RUN useradd -m -u 1000 app
USER app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    # Writable state lives outside the code tree; the app derives every other
    # runtime directory from this one.
    STORAGE_DIR=/home/app/data \
    HF_HOME=/home/app/.cache/huggingface \
    FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed

COPY --from=deps --chown=app:app /opt/venv /opt/venv

WORKDIR /home/app/backend
COPY --chown=app:app backend/app ./app
COPY --chown=app:app models /home/app/models

# Pre-download the embedding model into the image.
RUN python -c "\
from fastembed import TextEmbedding; \
TextEmbedding(model_name='sentence-transformers/all-MiniLM-L6-v2'); \
print('embedding model cached')"

ENV CV_MODEL_DIR=/home/app/models

# Platforms commonly expect 7860; PORT overrides it.
ENV PORT=7860
EXPOSE 7860

# Nested quoting in a HEALTHCHECK is a classic way to ship a container that
# reports unhealthy for a reason nobody can see, so this stays single-quoted
# throughout. start-period is generous because the first boot creates tables.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT','7860') + '/health').read()"

# One worker on purpose: the scheduler runs in-process and an embedded vector
# store takes an exclusive directory lock. Both are fine for a single
# container and both would break under --workers >1.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1"]
