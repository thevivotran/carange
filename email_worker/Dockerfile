FROM python:3.12-slim

WORKDIR /app

# Base carange deps (shared layer with the app image)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Worker-specific deps
COPY email_worker/requirements.txt ./email_worker/requirements.txt
RUN pip install --no-cache-dir -r email_worker/requirements.txt

ENV DATABASE_URL=sqlite:////data/carange.db
ENV PYTHONUNBUFFERED=1

COPY . .

RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

CMD ["python", "-m", "email_worker.worker"]
