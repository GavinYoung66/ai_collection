"""Milvus kb_vectors collection 初始化脚本"""
from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection

COLLECTION_NAME = "kb_vectors"
DIMENSION = 1024


def init():
    connections.connect(host="localhost", port="19530")

    fields = [
        FieldSchema(name="chunk_id", dtype=DataType.INT64, is_primary=True),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=DIMENSION),
        FieldSchema(name="doc_id", dtype=DataType.INT64),
        FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="version", dtype=DataType.INT64),
        FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=255),
    ]
    schema = CollectionSchema(fields=fields, description="知识库向量索引，与 PostgreSQL kb_chunks 1:1")
    collection = Collection(name=COLLECTION_NAME, schema=schema)

    collection.create_index(
        field_name="vector",
        index_params={"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 1024}},
    )
    collection.load()
    print(f"Collection {COLLECTION_NAME} created and loaded.")


if __name__ == "__main__":
    init()
