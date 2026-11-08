"""T125：prompt loader 进程级 YAML 解析缓存测试。"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
import yaml

from common.prompt import loader
from common.prompt.errors import PromptNotFoundError

_TEST_SERVICE = "t125-test-service"


def _bind_templates_dir(monkeypatch, tmp_path: Path) -> None:
    """把测试服务映射到临时目录，并屏蔽环境变量覆盖。"""
    monkeypatch.setitem(loader._SERVICE_TEMPLATE_DIRS, _TEST_SERVICE, tmp_path)
    monkeypatch.delenv(loader._TEMPLATES_DIR_ENV, raising=False)


def _write_template(tmp: Path, name: str, data: dict) -> Path:
    p = tmp / name
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
    return p


def _base_sections() -> dict:
    return {
        "role": "你是助手，处理 {{ case_id }}",
        "task": "完成任务，用户说 {{ user_text }}",
        "input_constraint": "输入约束",
        "output_schema": "SchemaX",
        "prohibitions": ["禁止项1"],
        "fallback": "兜底规则",
        "enum_constraints": ["枚举约束1"],
        "few_shot": "示例 {{ case_id }}",
    }


@pytest.fixture(autouse=True)
def _clear_yaml_cache():
    """每个用例前后清空 loader 解析缓存，保证计数断言干净。"""
    loader._LOCAL_YAML_CACHE.clear()
    yield
    loader._LOCAL_YAML_CACHE.clear()


def _counting_safe_load(monkeypatch, tmp_path) -> dict:
    """替换 loader 可见的 yaml.safe_load 为计数包装，仅统计本用例 tmp_path 下的解析。

    T125 加固留痕：safe_load 为全局 yaml 模块属性，全量跑时其他测试遗留线程的
    YAML 解析若被计入会使计数断言偶发失败（flaky），按文件路径过滤隔离。
    """
    calls = {"n": 0}
    real = yaml.safe_load

    def counting(data):
        if str(tmp_path) in str(getattr(data, "name", "")):
            calls["n"] += 1
        return real(data)

    monkeypatch.setattr(loader.yaml, "safe_load", counting)
    return calls


def test_consecutive_loads_parse_once(monkeypatch, tmp_path):
    """连续两次 load 同一模板：yaml.safe_load 仅调用一次，第二次命中缓存。"""
    _bind_templates_dir(monkeypatch, tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    calls = _counting_safe_load(monkeypatch, tmp_path)
    out1 = loader.load_prompt("test_pid", service=_TEST_SERVICE, case_id="C1", user_text="hi")
    assert calls["n"] == 1
    out2 = loader.load_prompt("test_pid", service=_TEST_SERVICE, case_id="C2", user_text="hi")
    assert calls["n"] == 1
    assert "C1" in out1 and "C2" in out2


def test_cache_invalidated_after_content_change(monkeypatch, tmp_path):
    """文件内容变更（size 变化）后缓存自动失效，重新解析出新内容。"""
    _bind_templates_dir(monkeypatch, tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0",
        "sections": {**_base_sections(), "role": "旧版提示词"},
    })
    out1 = loader.load_prompt("test_pid", service=_TEST_SERVICE)
    assert "旧版提示词" in out1
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0",
        "sections": {**_base_sections(), "role": "新版提示词"},
    })
    out2 = loader.load_prompt("test_pid", service=_TEST_SERVICE)
    assert "新版提示词" in out2
    assert "旧版提示词" not in out2


def test_cache_invalidated_after_mtime_only_change(monkeypatch, tmp_path):
    """仅 mtime 变化（内容/size 不变）同样触发重新解析，结果保持一致。"""
    _bind_templates_dir(monkeypatch, tmp_path)
    p = _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    calls = _counting_safe_load(monkeypatch, tmp_path)
    out1 = loader.load_prompt("test_pid", service=_TEST_SERVICE)
    assert calls["n"] == 1
    st = p.stat()
    # 仅推进 mtime 1 秒，内容与 size 不变
    os.utime(p, ns=(st.st_atime_ns + 10**9, st.st_mtime_ns + 10**9))
    out2 = loader.load_prompt("test_pid", service=_TEST_SERVICE)
    assert calls["n"] == 2
    assert out1 == out2


def test_new_file_picked_up(monkeypatch, tmp_path):
    """缓存生效期间新增模板文件仍会被扫描到并加载。"""
    _bind_templates_dir(monkeypatch, tmp_path)
    _write_template(tmp_path, "a_pid.yaml", {
        "prompt_id": "a_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    assert loader.load_prompt("a_pid", service=_TEST_SERVICE)
    _write_template(tmp_path, "b_pid.yaml", {
        "prompt_id": "b_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    out = loader.load_prompt("b_pid", service=_TEST_SERVICE)
    assert isinstance(out, str) and out.strip()


def test_prompt_not_found_semantics_kept(monkeypatch, tmp_path):
    """PromptNotFoundError 语义保持；未命中不缓存负结果，补写文件后可加载。"""
    _bind_templates_dir(monkeypatch, tmp_path)
    with pytest.raises(PromptNotFoundError):
        loader.load_prompt("ghost_pid", service=_TEST_SERVICE)
    _write_template(tmp_path, "ghost_pid.yaml", {
        "prompt_id": "ghost_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    out = loader.load_prompt("ghost_pid", service=_TEST_SERVICE)
    assert isinstance(out, str) and out.strip()


def test_version_semantics_kept(monkeypatch, tmp_path):
    """@version 精确匹配 / 错误版本抛错 / 无版本取 glob 第一个，语义不变。"""
    _bind_templates_dir(monkeypatch, tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    _write_template(tmp_path, "test_pid_v2.yaml", {
        "prompt_id": "test_pid", "version": "2.0.0", "sections": _base_sections(),
    })
    out_v1 = loader.load_prompt("test_pid@1.0.0", service=_TEST_SERVICE)
    out_v2 = loader.load_prompt("test_pid@2.0.0", service=_TEST_SERVICE)
    assert isinstance(out_v1, str) and out_v1.strip()
    assert isinstance(out_v2, str) and out_v2.strip()
    with pytest.raises(PromptNotFoundError):
        loader.load_prompt("test_pid@9.9.9", service=_TEST_SERVICE)
    # 无版本：取 glob 枚举第一个匹配文件（与原实现一致，含缓存场景）
    first_path = next(iter(tmp_path.glob("*.yaml")))
    with open(first_path, "r", encoding="utf-8") as f:
        first_version = yaml.safe_load(f)["version"]
    data = loader.load_prompt_sections("test_pid", service=_TEST_SERVICE)
    assert data["version"] == first_version


def test_returned_dict_isolated_from_cache(monkeypatch, tmp_path):
    """返回 dict 为独立副本：调用方修改不污染缓存，后续 load 不受影响。"""
    _bind_templates_dir(monkeypatch, tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    data1 = loader.load_prompt_sections("test_pid", service=_TEST_SERVICE)
    data1["sections"]["role"] = "被污染的文案"
    data1["polluted"] = True
    data2 = loader.load_prompt_sections("test_pid", service=_TEST_SERVICE)
    assert data2["sections"]["role"] == "你是助手，处理 {{ case_id }}"
    assert "polluted" not in data2
    # 缓存内部对象同样未被污染
    assert all("polluted" not in v for v in loader._LOCAL_YAML_CACHE.values())


def test_concurrent_cold_load_parses_once(monkeypatch, tmp_path):
    """多线程并发首次 load：double-check 锁内仅解析一次，全部成功。"""
    _bind_templates_dir(monkeypatch, tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    calls = _counting_safe_load(monkeypatch, tmp_path)
    results: list[str] = []
    errors: list[Exception] = []

    def worker(case_id: str):
        try:
            results.append(
                loader.load_prompt("test_pid", service=_TEST_SERVICE, case_id=case_id)
            )
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(f"C{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(results) == 8
    # 顺序无关断言留痕：results 由各线程 append，完成顺序不定，不能按索引对应
    # case_id（原断言属测试自身竞态缺陷，负载高时偶发失败，与 loader 缓存行为无关）
    for i in range(8):
        assert any(f"C{i}" in r for r in results)
    assert calls["n"] == 1