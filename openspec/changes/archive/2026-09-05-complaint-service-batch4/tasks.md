# Tasks: complaint-service-batch4

## 执行顺序

**串行：6.1 → 6.2 → 6.3 → 版本/A-B → 单测**

验证命令中 `PYTHONPATH` 用相对路径：`PYTHONPATH=../..:$PYTHONPATH`（在 `services/complaint-service/` 下执行时指向仓库根）。
环境：conda env `ai_collection`（Python≥3.11）。

多版本策略（design D6）：单文件 `{prompt_id}.yaml` + 文件内 `version` 匹配；A/B 选择逻辑可测；双版本正文可用测试 registry/夹具，本批不建多文件目录。

---

## 6.1 Prompt 加载器

- [x] 创建 `app/prompt/loader.py`
  - `load_prompt(prompt_id: str, **context_vars) -> str`
  - 可选 `session_id=`：无 `@version` 时先 `ab.select_version` 再加载（或文档约定由调用方先 select——实现须在 loader 或导出辅助函数中二选一且可测）
  - dev：从 `app/prompt/templates/{prompt_id}.yaml` 加载
  - prod：Nacos——本批 stub（`_load_from_nacos` 抛 NotImplementedError 或回退本地），不得假装已接真 Nacos
  - 支持 `prompt_id@version`：与文件内 version 一致才成功；不一致抛 `PromptVersionNotFoundError`（或等价）
  - Jinja2 逐段渲染后拼接（含 `few_shot`）
  - 结构校验：七段 `role/task/input_constraint/output_schema/prohibitions/fallback/enum_constraints` + 键 `few_shot` 必须存在；**不要**要求 `examples`
- [x] 创建 `app/prompt/errors.py`（可选）：`PromptNotFoundError` / `PromptStructureError` / `PromptVersionNotFoundError`
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.prompt.loader import load_prompt
  s = load_prompt('complaint_verdict')
  assert isinstance(s, str) and s.strip()
  s2 = load_prompt('complaint_verdict@1.0.0')
  assert isinstance(s2, str) and s2.strip()
  try:
      load_prompt('complaint_verdict@9.9.9')
      raise SystemExit('expected version error')
  except Exception as e:
      assert '9.9.9' in str(e) or type(e).__name__
  print('6.1 load + version match OK')
  "
  ```

## 6.2 八模板元数据/七段校验补齐

- [x] 遍历 `app/prompt/templates/*.yaml`（8 个业务模板）：
  - 确认元数据：prompt_id / version / target_model / scene / change_log / guardrails
  - 确认七段含 **`enum_constraints`**；确认存在 **`few_shot`**（可空）
  - 缺失则补默认/空占位；**禁止删除/清空已有正文**；**禁止**改用 `examples` 替代
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import yaml
  from pathlib import Path
  from app.prompt.loader import load_prompt
  root = Path('app/prompt/templates')
  ids = ['complaint_verdict','complaint_reflection','complaint_fact_timeline','complaint_compliance','complaint_user_demand','complaint_generate_package','evidence_case_v1','evidence_call_qc_v1']
  seven = ['role','task','input_constraint','output_schema','prohibitions','fallback','enum_constraints']
  for pid in ids:
      doc = yaml.safe_load((root / f'{pid}.yaml').read_text())
      sec = doc['sections']
      for k in seven:
          assert k in sec, f'{pid} missing {k}'
      assert 'few_shot' in sec, f'{pid} missing few_shot'
      assert 'enum_constraints' in sec
      # examples 不是权威第七段；不得替代 enum_constraints
      s = load_prompt(pid)
      assert isinstance(s, str) and s.strip()
  print(f'6.2 all {len(ids)} templates structure OK')
  "
  ```

## 6.3 model_router.yaml 增量增强

- [x] 修改 `app/llm/model_router.yaml`（**增量，保留 routes**）：
  - 新增 `environments: {dev, pre, prod}`（配置预留）
  - `breaker.qwen_plus.p95_threshold_ms: 800`
  - 新增 `provider_configs: {deepseek, qwen}`
  - **默认不改** `app/llm/router.py` 的 `route()` 读 environments（除非 tasks 外另有明确项）
- [x] 验证（批次3 不回归）：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.llm.router import ModelRouter
  import yaml
  from pathlib import Path
  r = ModelRouter()
  assert r.route('compliance') == 'deepseek-r1'
  assert r.route('reflection') == 'qwen-turbo'
  assert r.route('generate_package') == 'qwen-plus'
  cfg = yaml.safe_load(Path('app/llm/model_router.yaml').read_text())
  assert cfg['breaker']['qwen_plus']['p95_threshold_ms'] == 800
  assert 'environments' in cfg and 'provider_configs' in cfg
  print('6.3 router regression + enhancements OK')
  "
  ```

## 版本/A-B 流量管理

- [x] 创建 `app/prompt/ab.py`
  - `select_version(prompt_id, session_id) -> str`
  - `configure_ab(prompt_id, distribution: dict[str, int]) -> None`（百分比和为 100）
  - `rollback(prompt_id, version) -> None`（进程内立即 100%）
- [x] 创建 `tests/unit/test_prompt_ab.py`（比例 ±10%、同 session 稳定、rollback）
- [x] 与 loader 串联可测（`session_id=` 或先 select 再 `@ver`）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/test_prompt_ab.py -v
  ```

## 单测

- [x] 创建 `tests/unit/test_prompt_loader.py`
  - 渲染：对**声明的** Jinja 变量断言已替换（不要仅用「整串无 `{{`」）
  - `@version` 匹配成功 / 错误版本失败
  - 缺 `enum_constraints` 或 `few_shot` 键抛结构错误（可用临时夹具文件）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/test_prompt_loader.py tests/unit/test_prompt_ab.py -v
  ```

---

## 全部完成标准

- [x] 8 个 YAML 均可被 `load_prompt` 加载并渲染
- [x] `prompt_id@version` 与文件内 version 一致可加载；不一致失败
- [x] A/B `select_version` 可单测；rollback 立即生效
- [x] 七段键为 enum_constraints（非 examples）；few_shot 键存在；正文未清空
- [x] model_router 增强后 route 不回归；qwen-plus P95=800 已配置
- [x] Nacos 仅 stub；6.4 未建编码任务；未实现 LangGraph / 业务 API
- [x] 未建生产多文件版本目录（D6）
