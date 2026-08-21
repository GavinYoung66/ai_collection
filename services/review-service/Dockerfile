FROM python:3.11.15-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY common/ ./common/
COPY services/review-service/ ./app/
ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
