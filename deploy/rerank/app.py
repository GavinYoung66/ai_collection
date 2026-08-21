import os

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder

app = FastAPI()
# 模型路径：默认项目根 models/ 目录，可用 BGE_RERANK_PATH 环境变量覆盖
MODEL_PATH = os.environ.get("BGE_RERANK_PATH", "/Users/gavinyoung/my_project/my_work/ai_collection/models/bge-reranker-large")
model = CrossEncoder(MODEL_PATH)


class RerankRequest(BaseModel):
    query: str
    candidates: list[dict]  # [{chunk_id, content}]


@app.get("/health")
def health():
    return {"status": "healthy", "model": "bge-reranker-large"}


@app.post("/rerank")
def rerank(req: RerankRequest):
    pairs = [[req.query, c["content"]] for c in req.candidates]
    scores = model.predict(pairs)
    results = [{"chunk_id": c["chunk_id"], "rerank_score": float(s)} for c, s in zip(req.candidates, scores)]
    results.sort(key=lambda x: x["rerank_score"], reverse=True)
    return {"results": results}
