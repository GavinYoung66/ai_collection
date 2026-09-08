# 投诉人审前端（V2 最小静态页）

## 启动

后端先在 **8006** 就绪，然后：

```bash
cd services/complaint-service/web
python -m http.server 8080
```

浏览器打开：<http://localhost:8080>

默认 `API_BASE=http://localhost:8006`（页眉可改；也可用 `?api=http://127.0.0.1:8006`）。

## 能力

- 列表：id / 摘要 / 状态 / verdict
- 详情：投诉文本、判责、置信度、答辩草稿、可折叠 agent-logs
- 复核：`approve` + `reject_rejudge`（reason=`wrong_verdict`）
- 关单：`PUT .../close`（需已 `resolved`）

无登录、无批量运营台。CORS 已在后端放行 `localhost:8080`。
