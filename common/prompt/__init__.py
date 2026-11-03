"""common.prompt：平台统一 Prompt 加载体系（T30 后唯一入口）。

- ``loader``：结构化 Prompt（prompt_id@version + 七段 profile 校验 + Jinja 渲染）；
- ``local``：本地资源文件加载（dialog / fallback 话术）；
- ``ab``：A/B 版本选择；``telemetry``：Prompt 元数据埋点。
"""
from common.prompt.local import load_dialog_prompt, load_fallback_scripts, load_yaml

__all__ = ["load_dialog_prompt", "load_fallback_scripts", "load_yaml"]
