FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATABASE_URL=sqlite:////data/carange.db
ENV PYTHONUNBUFFERED=1

EXPOSE 6868

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "6868", "--workers", "2"]
