# 质检演示前端（静态页）

## 启动

后端先在 **8003** 就绪（CORS 已放行 `localhost:8080`），然后：

```bash
cd services/qc-service/web
python -m http.server 8080
```

浏览器打开：<http://localhost:8080>

默认 `API_BASE=http://localhost:8003`（页眉可改；也可用 `?api=http://127.0.0.1:8003`；会写入 localStorage）。

健康检查打服务根 `GET /health`（不是 `/api/v1/health`）。

## 能力

- 灌数：「模拟三条待质检通话」→ `POST /api/v1/qc/demo/seed`，token 自动填入
- 列表：本地已灌会话 ∪ `GET /api/v1/qc/demo/pending`（不用 DP pending-qc）
- 待检行：`POST /api/v1/qc/replay/{session_id}`；已出分行只拉 `GET /api/v1/qc/records/{session_id}`
- 人审：`CONFIRM` / `CORRECT`（手填 0–100）；页面不暴露驳回类按钮
- 时间线：`GET /api/v1/qc/demo/{session_id}/trace`；有 `langsmith_url` 才显示外链

无 React、无构建、无登录 SPA。不要与投诉 `web/` 合并。
