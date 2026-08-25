"""行级权限服务：加载 conf/row_access_matrix.yaml，按 (role, department) 过滤候选 category。

配置 Schema：
    {"(role, dept)": [category list]}

- JWT 缺失 → 原样返回 items（V1 兼容）。
- JWT 缺 department 或为 "UNKNOWN" → 走 (role, default)。
- (role, dept) 无匹配 → 走 (role, default)。
- 仍无匹配 → (default, default) 保底放行全部 4 类（V1 兼容）。
- category_filter 非空时与 allowed 集合取交集。
- 不抛 403；空结果由下游 fallback gate 处理。
"""
from __future__ import annotations

from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger()

# 默认权限矩阵路径：services/rag-service/conf/row_access_matrix.yaml
_DEFAULT_MATRIX_PATH = (
    Path(__file__).resolve().parent.parent.parent / "conf" / "row_access_matrix.yaml"
)

# 保底全量放行的 4 类 category（V1 兼容）
_ALL_CATEGORIES = {"regulation", "script", "case", "other"}


class RowAccessService:
    """行级权限服务：按 JWT 的 role/department 过滤候选 chunk 的 category。"""

    def __init__(self, path: str | Path | None = None):
        self._matrix: dict[tuple, list[str]] = {}
        self._has_three_tuple: bool = False
        self.load(path or _DEFAULT_MATRIX_PATH)

    def load(self, path: str | Path) -> None:
        """读取行级权限矩阵 YAML。

        Schema: ``{"(role, dept)": [category list]}``，键解析为 ``(role, dept)`` 元组。
        支持三元组键 ``(tenant_id, role, dept)``（V2 tenant_id bypass）。
        文件缺失/解析失败 → 空矩阵，所有查找走保底全量放行。
        """
        p = Path(path)
        if not p.exists():
            logger.error("row_access_matrix_not_found", path=str(p))
            self._reset()
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as e:
            logger.error("row_access_matrix_load_failed", path=str(p), error=str(e))
            self._reset()
            return

        if not data or not isinstance(data, dict):
            logger.warning("row_access_matrix_empty", path=str(p))
            self._reset()
            return

        matrix: dict[tuple, list[str]] = {}
        has_three = False
        for key_str, cats in data.items():
            if not isinstance(key_str, str):
                continue
            parsed = self._parse_key(key_str)
            if not parsed:
                continue
            if len(parsed) >= 3:
                has_three = True
            if not isinstance(cats, list):
                cats = []
            cats = [c for c in cats if isinstance(c, str) and c]
            matrix[parsed] = cats

        self._matrix = matrix
        self._has_three_tuple = has_three
        logger.info(
            "row_access_matrix_loaded",
            path=str(p),
            entries=len(matrix),
            has_three_tuple=has_three,
        )

    def _reset(self) -> None:
        """置空矩阵；所有查找将走保底全量放行。"""
        self._matrix = {}
        self._has_three_tuple = False

    @staticmethod
    def _parse_key(key_str: str) -> tuple:
        """解析 YAML 键字符串 '(ROLE, DEPT)' 或 '(TENANT, ROLE, DEPT)' 为元组。"""
        s = key_str.strip()
        if s.startswith("(") and s.endswith(")"):
            s = s[1:-1]
        parts = [p.strip() for p in s.split(",") if p.strip()]
        return tuple(parts)

    def _resolve_allowed(
        self, tenant_id: str | None, role: str, dept: str
    ) -> set[str]:
        """按优先级链解析允许的 category 集合。

        优先级：
            1. (tenant_id, role, dept) —— 仅当 tenant_id 存在且矩阵含三元组键
            2. (role, dept)
            3. (role, "default")
            4. (default, default) —— 保底全量放行 4 类
        """
        # 1. 三元组（V2 tenant_id bypass）
        if tenant_id and self._has_three_tuple:
            key = (tenant_id, role, dept)
            if key in self._matrix:
                return set(self._matrix[key])

        # 2. (role, dept)
        key = (role, dept)
        if key in self._matrix:
            return set(self._matrix[key])

        # 3. (role, default)
        key = (role, "default")
        if key in self._matrix:
            return set(self._matrix[key])

        # 4. (default, default) 保底 → 全量放行（V1 兼容）
        key = ("default", "default")
        if key in self._matrix:
            return set(self._matrix[key])
        return set(_ALL_CATEGORIES)

    def get_default_matrix(self) -> dict[tuple, list[str]]:
        """返回 fail-closed 保底矩阵：``(default, default) → 全量 4 类放行``。

        与运行时加载的 YAML 矩阵无关，始终可用；供行级权限过滤异常时的
        fail-closed 重放过滤兜底（对齐 search-enhancements spec「矩阵异常→
        降级 (default,default) 全量放行，与 V1 兼容」）。
        """
        return {("default", "default"): sorted(_ALL_CATEGORIES)}

    def filter(
        self,
        items: list[dict],
        jwt_payload: dict | None,
        category_filter: str | None = None,
    ) -> list[dict]:
        """按行级权限矩阵过滤候选 items。

        Args:
            items: 候选 chunk 列表，每项含 category 字段。
            jwt_payload: JWT 载荷；None 时原样返回（V1 兼容）。
            category_filter: 可选类别过滤，与 allowed 集合取交集。

        Returns:
            过滤后的 items 列表；空结果合法，不抛 403。
        """
        if jwt_payload is None:
            return items

        raw_role = jwt_payload.get("role")
        role = str(raw_role).upper() if raw_role else "default"

        raw_dept = jwt_payload.get("department")
        if not raw_dept or str(raw_dept).upper() == "UNKNOWN":
            dept = "default"
        else:
            dept = str(raw_dept)

        tenant_id = jwt_payload.get("tenant_id")
        if tenant_id is not None:
            tenant_id = str(tenant_id)

        allowed = self._resolve_allowed(tenant_id, role, dept)

        # category_filter 非空时取交集
        if category_filter is not None:
            allowed = allowed & {category_filter}

        result = [item for item in items if item.get("category") in allowed]
        logger.info(
            "row_access_filtered",
            role=role,
            dept=dept,
            tenant_id=tenant_id,
            allowed=sorted(allowed),
            category_filter=category_filter,
            before=len(items),
            after=len(result),
        )
        return result
