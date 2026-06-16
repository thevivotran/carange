FROM python:3.14-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV DATABASE_URL=postgresql://carange:carange@localhost:5432/carange
ENV PYTHONUNBUFFERED=1
COPY . .
RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app
CMD ["python", "-m", "notify_worker.worker"]
