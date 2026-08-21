# AI 电话催收生态赋能系统

> 规范驱动开发（Specification-Driven Development）项目，7 个 AI 大模型应用子系统。

## 技术栈

| 类别 | 选型 | 版本 |
|---|---|---|
| 语言/运行时 | Python | 3.11.15 |
| Web 框架 | FastAPI + Uvicorn | 0.104.1 / 0.24.0 |
| ORM/迁移 | SQLAlchemy + Alembic | 2.0.23 / 1.3.0 |
| 数据库 | PostgreSQL | 15 |
| 缓存 | Redis | 7 |
| 向量库 | Milvus + BGE-M3 | 2.4 / 1024 维 |
| 稀疏检索 | Elasticsearch | 8 |
| 重排序 | bge-reranker (rerank-service:8101) | — |
| AI 编排 | LangChain + LangGraph | 0.1 / 0.0.40 |
| 大模型 | 阿里云 DashScope qwen-plus | — |

## 快速开始

```bash
# 1. 创建 conda 环境
conda create -n ai_collection python=3.11.15
conda activate ai_collection

# 2. 安装依赖
make compile  # 生成 requirements.lock
make install  # 安装依赖

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入真实密钥

# 4. 启动基础设施
make dev

# 5. 数据库迁移
make migrate

# 6. 冒烟测试
make smoke
```

## 7 服务端口

| # | 服务 | 端口 | 里程碑 |
|---|---|---|---|
| 1 | data-platform | 8001 | M1 |
| 2 | rag-service | 8002 | M1 |
| 3 | qc-service | 8003 | M1 |
| 4 | call-service | 8004 | M2 |
| 5 | case-service | 8005 | M2 |
| 6 | complaint-service | 8006 | M3 |
| 7 | review-service | 8007 | M4 |

辅助服务：embedding-service (8100) / rerank-service (8101) / Elasticsearch (9200)

## 目录结构

```
ai_collection/
├── services/          # 7 子系统代码
├── common/            # 公共能力层（9 个包）
├── deploy/            # embedding/rerank Dockerfile + nginx
├── migrations/        # Alembic 迁移
├── scripts/           # smoke 脚本 + seed 数据
├── docs/              # 设计文档归档
├── 标准工程目录结构/  # 规范层（step1~step8）
├── openspec/          # 宪法层（specs）
└── demand/            # 历史参考（只读）
```

## 规范文档索引

| 层级 | 位置 | 职责 |
|---|---|---|
| 宪法层 | openspec/specs/ | 强制红线 |
| 规范层 | 标准工程目录结构/step1~step8 | 详细规格 |
| 参考源 | demand/ | 历史材料（只读） |
