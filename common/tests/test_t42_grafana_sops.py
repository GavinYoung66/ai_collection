"""T42：Grafana 管理员密码纳入 sops 加密体系的仓库侧守护。

验收判据（specs/platform/secrets-sops「监控口令纳入加密体系」）：
- 示例环境文件（.env.example）中该口令 MUST 为占位值；
- 生产值 MUST 由加密注入提供（compose 经 ${VAR:?} 强制注入、缺失即拒启；
  .env.enc 若含该键 MUST 为 sops 密文，MUST NOT 明文）；
- .sops.yaml 的 encrypted_regex MUST 命中该键名（纳入体系后由 sops 自动加密）。

⚠ 人工运维动作（age 私钥离线持有，AI 不可代执行）：核心成员以
`SOPS_AGE_KEY_FILE=<私钥> sops --input-type dotenv -e .env.enc` 添加
GRAFANA_ADMIN_PASSWORD 强随机值并重加密（正则自动命中 → 存为 ENC[...]）。
本测试对该动作完成后生效：一旦 .env.enc 出现明文该键即变红。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"
ENV_ENC = ROOT / ".env.enc"
SOPS_YAML = ROOT / ".sops.yaml"
COMPOSE = ROOT / "docker-compose.yml"

KEY = "GRAFANA_ADMIN_PASSWORD"
_PLACEHOLDERS = {"", "change_me", "<change_me>", "changeme", "change-me", "none", "null"}


def _active_lines(text: str) -> list[tuple[str, str]]:
    """解析 dotenv 行为 (key, value)，忽略注释与空行。"""
    pairs = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        pairs.append((k.strip(), v.strip()))
    return pairs


class TestEnvExamplePlaceholder:
    def test_key_present_in_example(self):
        """正常流程：示例文件已登记该键（纳入体系的可发现入口）。"""
        keys = {k for k, _ in _active_lines(ENV_EXAMPLE.read_text(encoding="utf-8"))}
        assert KEY in keys

    def test_example_value_is_placeholder(self):
        """验证判据 1：示例文件 MUST 为占位值，MUST NOT 携带真实口令。"""
        pairs = dict(_active_lines(ENV_EXAMPLE.read_text(encoding="utf-8")))
        assert pairs[KEY].strip().lower() in _PLACEHOLDERS


class TestEncryptedCarrier:
    def test_env_enc_has_no_plaintext_value(self):
        """验证判据 2：密文载体若含该键，MUST 为 ENC[...] 密文（明文零容忍，含注释行）。"""
        if not ENV_ENC.exists():
            return
        text = ENV_ENC.read_text(encoding="utf-8")
        for line in text.splitlines():
            if KEY in line:
                stripped = line.strip()
                if stripped.startswith("#"):
                    pytest.fail(f".env.enc 注释行提及 {KEY}，须确认未记录真实值：{line!r}")
                _, _, value = stripped.partition("=")
                assert value.strip().startswith("ENC["), (
                    f".env.enc 中 {KEY} 为明文，必须经 sops 重加密为 ENC[...] 密文"
                )

    def test_sops_regex_covers_key(self):
        """纳入确认：.sops.yaml encrypted_regex 命中该键（添加即自动加密）。"""
        text = SOPS_YAML.read_text(encoding="utf-8")
        m = re.search(r"encrypted_regex:\s*['\"]?\(([^)]+)\)", text)
        assert m, ".sops.yaml 缺少 encrypted_regex"
        pattern = m.group(1)
        assert re.search(pattern, KEY), f"encrypted_regex ({pattern}) 未命中 {KEY}"


class TestComposeInjection:
    def test_grafana_reads_required_env(self):
        """生产值经加密注入：compose 以 ${KEY:?} 读取，未注入即拒绝启动（fail-fast）。"""
        text = COMPOSE.read_text(encoding="utf-8")
        assert re.search(rf"GF_SECURITY_ADMIN_PASSWORD: \${{{KEY}:\?", text), (
            "docker-compose.yml grafana 服务未以 ${" + KEY + ":?} 强制注入"
        )
        # 不得回退默认弱口令（如 ${KEY:-admin}）
        assert not re.search(rf"\$\{{{KEY}:-", text), f"compose 中 {KEY} 存在默认值回退"

