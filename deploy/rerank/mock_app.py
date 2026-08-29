"""Mock Rerank Service — 用于冒烟测试（基于关键词匹配返回 score）。"""
import re

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Mock Rerank Service")

_ENTITY_ID_RE = re.compile(r"CS\d{6,}", re.IGNORECASE)


class RerankRequest(BaseModel):
    query: str
    candidates: list[dict]


@app.get("/health")
def health():
    return {"status": "healthy", "model": "mock-bge-reranker"}


def _char_ngrams(text: str, n: int = 2) -> set:
    """字符级 n-gram 特征集合，用于中文/英文查询的相似度区分。

    去除空白后按字符切分，避免 `\\w+` 对中文整串只切出 1 个 token 导致全候选恒等；
    文本短于 n 时返回整串（非空）作为唯一特征，保证非空文本有特征。
    """
    text = re.sub(r"\s+", "", (text or "").lower())
    if not text:
        return set()
    if len(text) < n:
        return {text}
    return {text[i:i + n] for i in range(len(text) - n + 1)}


@app.post("/rerank")
def rerank(req: RerankRequest):
    query_grams = _char_ngrams(req.query)
    entity_ids = _ENTITY_ID_RE.findall(req.query)
    results = []
    for c in req.candidates:
        content = c.get("content") or ""
        if not content:
            score = 0.0
        elif entity_ids and not any(eid in content for eid in entity_ids):
            # 查询含业务实体 ID 时，未命中 ID 的候选强降权，减少噪声
            score = 0.05
        elif not query_grams:
            # 无有效查询特征时给稳定分，避免本地冒烟误走兜底（对齐原 0.8 兜底意图）
            score = 0.8
        else:
            content_grams = _char_ngrams(content)
            overlap = len(query_grams & content_grams)
            if overlap == 0:
                score = 0.0
            else:
                # 有共享字符特征即在 0.4~1.0 之间产生区分度，相关候选排前且能过闸门 2（0.35）
                ratio = overlap / max(len(query_grams), 1)
                score = 0.4 + 0.6 * min(ratio, 1.0)
                if entity_ids and any(eid in content for eid in entity_ids):
                    score = max(score, 0.92)
        results.append({"chunk_id": c["chunk_id"], "rerank_score": float(score)})
    results.sort(key=lambda x: x["rerank_score"], reverse=True)
    return {"results": results}
