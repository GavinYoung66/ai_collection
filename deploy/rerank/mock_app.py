"""Mock Rerank Service — 用于冒烟测试（基于关键词匹配返回 score）。"""
import re

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock Rerank Service")


class RerankRequest(BaseModel):
    query: str
    candidates: list[dict]


@app.get("/health")
def health():
    return {"status": "healthy", "model": "mock-bge-reranker"}


@app.post("/rerank")
def rerank(req: RerankRequest):
    query_terms = set(re.findall(r"[\w]+", req.query.lower()))
    results = []
    for c in req.candidates:
        content_terms = set(re.findall(r"[\w]+", c["content"].lower()))
        overlap = len(query_terms & content_terms)
        score = min(overlap / max(len(query_terms), 1), 1.0)
        if not query_terms:
            score = 0.5
        results.append({"chunk_id": c["chunk_id"], "rerank_score": float(score)})
    results.sort(key=lambda x: x["rerank_score"], reverse=True)
    return {"results": results}
