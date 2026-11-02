# pins-lock-exempt：sentence-transformers/torch 系为本服务独立安装依赖（不在根 requirements.lock 管辖）；
# 已探测债务：fastapi==0.104.1 与 T20 升级后根 lock（0.109.1）漂移，
# 待部署基线批次（T36）随镜像重建一并收敛，届时移除本标记。
FROM python:3.11.15-slim
WORKDIR /app
RUN pip install --no-cache-dir sentence-transformers==2.2.2 fastapi==0.104.1 uvicorn==0.24.0
COPY app.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
