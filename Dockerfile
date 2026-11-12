FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 robobo

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY robobo ./robobo
COPY main.py ./

USER robobo

# long polling：不需要暴露端口，也不需要域名
CMD ["python", "-m", "robobo"]
