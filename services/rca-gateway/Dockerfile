FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY services/rca-gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY services/rca-gateway/src /app/src
COPY datasets /app/datasets

ENV PYTHONPATH=/app
EXPOSE 7000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "7000"]
