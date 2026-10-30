# The sandbox DAB's execute_python tool runs agent code in (image python-data:3.12).
# Build: docker build -t python-data:3.12 -f stack/python-data.Dockerfile stack
# Versions are those of the image the published runs used.
FROM python:3.12.15-slim
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pandas==3.0.6 pyarrow==25.0.1 numpy==2.5.3
