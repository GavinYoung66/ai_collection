# LLM 配额与连通确认（14.3 / REQ-CJ-022 / REQ-CJ-023）

> 密钥、配额账号口令 **不得** 写入仓库；填「由部署/对接方线下确认」。
> dev/test 继续 `USE_MOCK_LLM=true`；pre/prod 真调用开关不在本批打开。
> 配额或连通未确认则全部 Agent 真推理不可用，缓解为 **Mock LLM** Provider。
> 本批不改 `common/llm` / complaint-service LLM 代码。

## 配额表

| provider | model | quota | qpm | qps | unit_price_input | unit_price_output | unit_price_reasoning | endpoint | network | tls | connectivity | owner_team | confirmed_date | gaps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DeepSeek | deepseek-r1 | TBD（由部署/对接方线下确认） | TBD | TBD | TBD | TBD | TBD | https://api.deepseek.com（规划基线，非合同） | TBD（公网/VPC/专线） | TBD | TBD | 算法/基础设施团队 | TBD | 本仓 `.env.example` 仅有 `DEEPSEEK_API_KEY` 空占位；DD-05 v1.1 路由以 qwen-plus 为主，deepseek-r1 为批次12 跟踪目标 |
| DashScope | qwen-turbo | TBD（由部署/对接方线下确认） | TBD | TBD | TBD | TBD | N/A | https://dashscope.aliyuncs.com/compatible-mode/v1 | TBD | TBD | TBD | 算法/基础设施团队 | TBD | 本仓 `common/llm/provider.py` 已用该 compatible-mode base_url；NFR 对照 qwen-turbo 30 QPS（非合同） |
| DashScope | qwen-plus | TBD（由部署/对接方线下确认） | TBD | TBD | TBD | TBD | N/A | https://dashscope.aliyuncs.com/compatible-mode/v1 | TBD | TBD | TBD | 算法/基础设施团队 | TBD | `.env.example` `LLM_API_KEY=<change_me>`；NFR 对照 qwen-plus 10 QPS（非合同） |

QPM 与 QPS 均须记录：若对接方只给其一，按 `QPM = QPS × 60` 换算并标注来源。上表数值在合同未到前保持 TBD。

### PRD 对照备注（不是已签字合同）

- DD-05 v1.1 §20.2：qwen-turbo 30 QPS；qwen-plus 10 QPS。
- 批次12 规划另跟踪 deepseek-r1（合规/判责推理模型）；与 v1.1 §8.5「合规用 qwen-plus」存在路由差异，记入 gaps，本批不改路由代码。

## reasoning_content 专节（deepseek-r1）

| 项 | 约定 | 确认 |
| --- | --- | --- |
| 字段名 | `reasoning_content`（DeepSeek 推理模型与 `content` 分列） | TBD（对接方确认实际 JSON 路径） |
| 类型 | string（思维链明文，落库前加密） | TBD |
| 与 content 分列 | yes：`content` 为最终 JSON 答案；`reasoning_content` 为 CoT | TBD |
| reasoning token 计费 | 单独计入 `unit_price_reasoning`；计入 `total_tokens_consumed` | TBD |
| CoT 存储 | 明文不得落库；AES-256-GCM 写入 `cot_encrypted`；密钥 KMS/部署注入，**不入库** | 代码侧本仓尚未实现 complaint CoT 字段；本批只对齐字段名 |
| DashScope qwen-turbo / qwen-plus | 无 reasoning 分列字段 | **N/A** |

## 连通与 Mock

- `connectivity`：`ok` / `fail` / `TBD`。当前三行均为 TBD（本批不对真实 API 发探测，避免密钥与配额问题）。
- 声明：继续 `USE_MOCK_LLM`；本批不打开真调用。
- API Key / 账号口令：由部署/对接方线下确认，仓库只保留 `<change_me>` / 空占位。
