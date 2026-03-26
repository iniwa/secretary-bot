FROM python:3.11-slim

# システム依存パッケージ
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# 非rootユーザー作成
RUN useradd --create-home --shell /bin/bash botuser

WORKDIR /app

# 依存ライブラリ（キャッシュ効率のため先にコピー）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ソースコード・config.yamlはVolumeマウント（COPYしない）

RUN chown -R botuser:botuser /app
USER botuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health')" || exit 1

CMD ["python", "-m", "src.bot"]
