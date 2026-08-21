FROM python:3.11.15-slim
WORKDIR /app
RUN pip install --no-cache-dir sentence-transformers==2.2.2 fastapi==0.104.1 uvicorn==0.24.0
COPY app.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
