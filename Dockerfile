FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY analysis.py app.py backfill_history.py config.py db.py predict_cli.py \
     scanner.py worker.py ./
COPY static ./static

ENV HOST=0.0.0.0
ENV PORT=8777
ENV ROLE=all
ENV PYTHONUNBUFFERED=1

EXPOSE 8777

CMD ["python", "app.py"]
