FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt requirements-scan.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-scan.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY tg_media_dedupe_bot ./tg_media_dedupe_bot
COPY app ./app
COPY scripts ./scripts
COPY healthcheck.sh /usr/local/bin/healthcheck.sh
COPY scripts/selfcheck.py /usr/local/bin/selfcheck.py
COPY scripts/smoke_tag.py /usr/local/bin/smoke_tag.py
COPY scripts/smoke_tag_rebuild_direction.py /usr/local/bin/smoke_tag_rebuild_direction.py
COPY web_app.py ./web_app.py
COPY config.json.example ./config.json.example

RUN chmod +x /usr/local/bin/healthcheck.sh /usr/local/bin/smoke_tag.py /usr/local/bin/smoke_tag_rebuild_direction.py \
    && mkdir -p /app/data /app/sessions /app/backups \
    && chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD /usr/local/bin/healthcheck.sh

ENTRYPOINT ["python"]
CMD ["web_app.py"]
