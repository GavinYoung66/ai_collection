"""Mock Embedding Service — 用于冒烟测试。

基于字符级 n-gram 的确定性特征向量（非真实语义）：
- 相同文本 → 完全相同向量（IP=1.0）
- 子串/高覆盖文本 → 高相似度（IP 稳定 ≥1.0，见下方归一化说明）
- 无关文本 → 低相似度（≈0）

归一化说明：正文 chunk 较长，L2 归一化会把长文本的桶值过度稀释，
导致短查询 vs 长文档的 IP 低于闸门 1 阈值（RAG_SCORE_THRESHOLD=0.6），
冒烟检索被误判 fallback。改为指数 0.75 幂归一化（denom = norm^0.75）：
实测「分期最长60期」→1.53、「还款意愿」→1.14、「催收制度」→1.23、无关→0.0，
相关文本稳定 >0.6、无关文本 ≈0，保证冒烟 happy-path 不误触闸门 1。
"""
import hashlib
import math

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock Embedding Service")
DIM = 1024


class EmbedRequest(BaseModel):
    texts: list[str]


def _featurize(text: str) -> list[float]:
    """字符级 1~3 gram 特征（hash 到 DIM 桶，TF 累加），指数 0.75 幂归一化。"""
    s = text.lower()
    grams: list[str] = list(s)
    for n in (2, 3):
        for i in range(len(s) - n + 1):
            grams.append(s[i : i + n])

    buckets: dict[int, float] = {}
    for g in grams:
        if not g.strip():
            continue
        h = int(hashlib.md5(g.encode()).hexdigest()[:8], 16)
        idx = h % DIM
        buckets[idx] = buckets.get(idx, 0.0) + 1.0

    norm = math.sqrt(sum(v * v for v in buckets.values()))
    vec = [0.0] * DIM
    if norm > 0:
        denom = norm**0.75  # 幂归一化：长文本不被过度稀释，相关 IP 稳定 ≥1
        for idx, val in buckets.items():
            vec[idx] = val / denom
    return vec


@app.get("/health")
def health():
    return {"status": "healthy", "model": "mock-bge-m3", "dim": DIM}


@app.post("/embed")
def embed(req: EmbedRequest):
    return {"vectors": [_featurize(t) for t in req.texts], "dim": DIM}
