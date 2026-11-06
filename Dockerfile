FROM node:24-bookworm-slim AS node
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates ffmpeg libstdc++6 && rm -rf /var/lib/apt/lists/* && groupadd -g 10001 site && useradd -u 10001 -g 10001 -M site && mkdir /data && chown 10001:10001 /data
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY requirements.txt requirements.compat.txt requirements.vendor.txt /opt/media-release/
RUN pip install --no-cache-dir -r /opt/media-release/requirements.compat.txt \
 && pip install --no-cache-dir --no-deps --target /opt/media-runtime -r /opt/media-release/requirements.vendor.txt
COPY app /site
ENV PYTHONPATH=/opt/media-runtime:/site SITE_YOUTUBE_PYTHONPATH=/site PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /site
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).close()"]
ENTRYPOINT ["python", "-m", "uvicorn", "app:app", "--app-dir", "/site", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
