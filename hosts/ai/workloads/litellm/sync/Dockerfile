FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY sync_models.py /app/sync_models.py

ENTRYPOINT ["python3", "/app/sync_models.py"]
