# PR 自检清单（complaint-service / 批次10+）
<!-- 提交 PR 前勾选 -->

- [ ] Unit + Integration 通过；**D8 核心覆盖率 ≥85%**（非整包 `--cov=app`）
- [ ] E2E 关键路径 0 失败（含 `complaint.judged` / `complaint.closed`）
- [ ] Prompt 回归：golden set 一致率 ≥95%（真跑判责/Mock 策略，禁止 tautology）
- [ ] 冒烟 `scripts/smoke/smoke_complaint_service.sh` 退出码 0（port 8006）
- [ ] 错误码符合 `common.errors.codes`；未新建第二 EventPublisher
- [ ] 压测脚本路径正确；若宣称达标则准入门槛已校验
- [ ] ruff / bandit / mypy：仓库有配置则已跑（无则 N/A）
