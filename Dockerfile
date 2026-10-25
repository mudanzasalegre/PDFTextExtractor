FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STORAGE_DIR=/app/storage \
    MAX_UPLOAD_MB=300 \
    MAX_TTS_UPLOAD_MB=50 \
    MAX_TTS_CHARS=750000 \
    MAX_WORKERS=2 \
    MAX_TTS_WORKERS=1 \
    PIPER_MODELS_DIR=/app/models/piper \
    PIPER_DEFAULT_VOICE=es_ES-sharvard-medium

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    espeak-ng \
    libgomp1 \
    tesseract-ocr \
    tesseract-ocr-spa \
    tesseract-ocr-eng \
    ocrmypdf \
    ghostscript \
    qpdf \
    unpaper \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modelos locales Piper para español de España. El modo TTS automático los usa
# como respaldo si no hay internet o si edge-tts falla.
RUN mkdir -p /app/models/piper && \
    curl -L --fail --retry 3 "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx?download=true" -o /app/models/piper/es_ES-sharvard-medium.onnx && \
    curl -L --fail --retry 3 "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx.json?download=true" -o /app/models/piper/es_ES-sharvard-medium.onnx.json && \
    curl -L --fail --retry 3 "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx?download=true" -o /app/models/piper/es_ES-davefx-medium.onnx && \
    curl -L --fail --retry 3 "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json?download=true" -o /app/models/piper/es_ES-davefx-medium.onnx.json && \
    curl -L --fail --retry 3 "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx?download=true" -o /app/models/piper/es_ES-carlfm-x_low.onnx && \
    curl -L --fail --retry 3 "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx.json?download=true" -o /app/models/piper/es_ES-carlfm-x_low.onnx.json

COPY app ./app
RUN mkdir -p /app/storage

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
