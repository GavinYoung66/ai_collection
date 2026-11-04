# pins-lock-exempt：sentence-transformers/torch 系为本服务独立安装依赖（不在根 requirements.lock 管辖）；
# T36 已把 fastapi / uvicorn 对齐根 lock（T20 升级后版本），保留豁免仅因 sentence-transformers 不在 lock。
FROM python:3.11.15-slim
WORKDIR /app
RUN pip install --no-cache-dir sentence-transformers==2.2.2 fastapi==0.109.1 uvicorn==0.24.0
COPY app.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
