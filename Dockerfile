FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Alpine 用 BusyBox adduser；镜像以非 root 用户运行
RUN adduser -D -u 10001 robobo

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY robobo ./robobo
COPY main.py ./

USER robobo

# long polling：不需要暴露端口，也不需要域名
CMD ["python", "-m", "robobo"]
