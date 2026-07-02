FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY src/main.py /app/main.py
COPY src/terno.py /app/terno.py
COPY manifest.json /app/manifest.json

RUN mkdir -p /logs

ENV PYTHONUNBUFFERED=1
# IMPORTANT: process reads from stdin (no CLI args except --version)
ENTRYPOINT ["python3", "/app/main.py"]
