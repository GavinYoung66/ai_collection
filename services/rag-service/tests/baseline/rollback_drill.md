# rag_answer A/B rollback 演练记录

```python
from common.prompt import ab
ab.configure_ab('rag_answer', {'1.0.0': 60, '1.1.0': 40})
ab.rollback('rag_answer', '1.0.0')
ab.select_version('rag_answer', session_id)  # 1000 次
```

调用点接入：`app/services/llm_providers/base.py` 模块导入时默认
`configure_ab('rag_answer', {'1.0.0': 100})`（未配置则 100% 基线，对齐 qc 先例），
`build_answer_prompt()` 每次组装前经 `select_version` 选版本。

## 运行输出

```
=== rag_answer A/B rollback 演练 ===
configure_ab -> {'1.0.0': 60, '1.1.0': 40}
rollback 前 10000 次采样（有 session_id）: {'1.0.0': 6094, '1.1.0': 3906}
rollback(rag_answer,1.0.0) -> {'1.0.0': 100}
rollback 后 1000 次采样: {'1.0.0': 1000}
断言: 1000/1000 均为 1.0.0 -> PASS
加载 rag_answer@1.0.0: prompt_id=rag_answer version=1.0.0 -> PASS
```

（rollback 时触发 `prompt_ab_rollback ... scope=process-local` warning 日志，属预期）

## 边界

`rollback` 仅对当前进程生效（`scope=process-local`，触发 warning 日志）。多副本部署时需对每个副本分别执行。
`build_answer_prompt` 暂无真实 session_id 入参，内部以 `rag-anon-{uuid4}` 合成会话标识参与分流；
单版本（100% 基线）下与真实会话粒度等价，后续多版本灰度时再穿参。
