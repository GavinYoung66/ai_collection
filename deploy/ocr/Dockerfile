FROM paddlepaddle:2.6.0-runtime
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.104.1 uvicorn==0.24.0 paddleocr==2.7.0.3 pdf2image==1.17.0 pillow==10.2.0
COPY app.py /app/app.py
COPY requirements.txt /app/requirements.txt
EXPOSE 8200
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200"]
