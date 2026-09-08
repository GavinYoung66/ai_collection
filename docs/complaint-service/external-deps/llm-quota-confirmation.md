# LLM 说明（个人项目 · Ellis Guo）

本地：**Mock LLM**。负责人：**Ellis Guo**。密钥不入库。

## 三模型（本地占位）

| provider | model | quota | qpm | qps | unit_price_input | unit_price_output | unit_price_reasoning | endpoint | network | tls | connectivity | owner_team | contact_name | confirmed_date | gaps |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DeepSeek | deepseek-r1 | unlimited-local | N/A | 5（PRD对照） | N/A | N/A | N/A | mock://local | local | N/A | ok（Mock） | Ellis Guo | Ellis Guo | 2026-09-07 | 本地 Mock，非真 API |
| DashScope | qwen-turbo | unlimited-local | N/A | 20（PRD对照） | N/A | N/A | N/A | mock://local | local | N/A | ok（Mock） | Ellis Guo | Ellis Guo | 2026-09-07 | 本地 Mock，非真 API |
| DashScope | qwen-plus | unlimited-local | N/A | N/A | N/A | N/A | N/A | mock://local | local | N/A | ok（Mock） | Ellis Guo | Ellis Guo | 2026-09-07 | 本地 Mock，非真 API |

## reasoning_content

| 项 | 值 |
| --- | --- |
| 字段名 | reasoning_content（约定） |
| 类型 | string |
| 与 content 分列 | 是 |
| 本地衔接 | cot_encrypted（AES-256-GCM）已实现 |
| 确认人 | Ellis Guo · 2026-09-07 |
