"""Mock Embedding Service — 用于冒烟测试（返回随机向量，非真实语义）。"""
import hashlib
import random

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock Embedding Service")
DIM = 1024


class EmbedRequest(BaseModel):
    texts: list[str]


@app.get("/health")
def health():
    return {"status": "healthy", "model": "mock-bge-m3", "dim": DIM}


@app.post("/embed")
def embed(req: EmbedRequest):
    vectors = []
    for text in req.texts:
        seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        vec = [rng.uniform(-1, 1) for _ in range(DIM)]
        vectors.append(vec)
    return {"vectors": vectors, "dim": DIM}
