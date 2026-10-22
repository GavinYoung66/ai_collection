# qc_review A/B rollback 演练记录

```python
from common.prompt import ab
ab.configure_ab('qc_review', {'1.0.0': 60, '1.1.0': 40})
ab.rollback('qc_review', '1.0.0')
ab.select_version('qc_review', session_id)  # 1000 次
```

## 运行输出

```
=== qc_review A/B rollback 演练 ===
configure_ab -> {'1.0.0': 60, '1.1.0': 40}
rollback 前 10000 次采样（有 session_id）: {'1.0.0': 6071, '1.1.0': 3929}
rollback('qc_review','1.0.0') -> {'1.0.0': 100}
rollback 后 1000 次采样: {'1.0.0': 1000}
断言: 1000/1000 均为 1.0.0 -> PASS
加载 qc_review@1.0.0: prompt_id=qc_review version=1.0.0 -> PASS
```

## 边界

`rollback` 仅对当前进程生效（`scope=process-local`，触发 warning 日志）。多副本部署时需对每个副本分别执行。
