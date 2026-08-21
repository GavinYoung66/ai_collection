"""数据访问层。"""
from app.repositories.chunk_repo import ChunkRepo
from app.repositories.doc_repo import DocRepo

__all__ = ["DocRepo", "ChunkRepo"]
