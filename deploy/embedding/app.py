import os

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

app = FastAPI()
# 模型路径：默认项目根 models/ 目录，可用 BGE_M3_PATH 环境变量覆盖
MODEL_PATH = os.environ.get("BGE_M3_PATH", "/Users/gavinyoung/my_project/my_work/ai_collection/models/bge-m3")
model = SentenceTransformer(MODEL_PATH)  # 1024 维


class EmbedRequest(BaseModel):
    texts: list[str]


@app.get("/health")
def health():
    return {"status": "healthy", "model": "bge-m3", "dim": 1024}


@app.post("/embed")
def embed(req: EmbedRequest):
    vectors = model.encode(req.texts)
    return {"vectors": vectors.tolist(), "dim": 1024}
