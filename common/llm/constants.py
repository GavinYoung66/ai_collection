"""LLM 层共享常量单一事实源（T44：消除多文件重复定义）。

历史上 `_DASHSCOPE_COMPAT_BASE` 在 `common/llm/provider.py`、
`common/llm/reliability/orchestrator.py`（原 reliability.py:243）与
`services/qc-service/app/services/llm_reviewer.py` 三处重复定义，
端点变更需三处同步、易漂移。本模块为该常量的唯一定义处，
其余位置一律导入。
"""
from __future__ import annotations

# DashScope OpenAI 兼容模式 Base URL（chat/completions 路径由 transport 拼接）。
DASHSCOPE_COMPAT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
