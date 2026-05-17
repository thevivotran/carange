FROM python:3.12-slim

WORKDIR /app

# System libs required by OpenCV and PaddlePaddle
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Base carange deps first (shared layer with the app image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Worker-specific deps in a separate layer so the heavy layer is cached
COPY ocr_worker/requirements.txt ./ocr_worker/requirements.txt
RUN pip install --no-cache-dir -r ocr_worker/requirements.txt

# Disable MKL-DNN/oneDNN to avoid double-free crash on CI/non-AVX2 hosts
ENV FLAGS_use_mkldnn=0
ENV DATABASE_URL=sqlite:////data/carange.db
ENV UPLOAD_DIR=/data/uploads
ENV PYTHONUNBUFFERED=1

# App code
COPY . .

RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

CMD ["python", "-m", "ocr_worker.worker"]
