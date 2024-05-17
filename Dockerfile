# The image deliberately does NOT contain a spaCy model.
# That is the same constraint the Lambda package lives under: the model is pulled
# from object storage at runtime and cached in /tmp. Keeping the container honest
# about it means what you test locally is what runs in Lambda.

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    MODEL_CACHE_DIR=/tmp/models \
    PORT=8320

# No compilers, no pip cache, no build tooling in the runtime stage.
COPY --from=builder /opt/venv /opt/venv

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser
WORKDIR /app
COPY --chown=appuser:appuser nlp_lambda ./nlp_lambda
COPY --chown=appuser:appuser app.py ./

USER appuser
EXPOSE 8320

# /health never touches S3 or the model, so it reports the container, not the bucket.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8320/health', timeout=2).status == 200 else 1)"]

# One worker, several threads: the model is ~50-500 MB of resident memory and each
# worker process would hold its own copy. See README, "Concurrency and memory".
CMD ["gunicorn", "--bind", "0.0.0.0:8320", "--workers", "1", "--threads", "4", \
     "--timeout", "120", "--access-logfile", "-", "app:app"]
