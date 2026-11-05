"""行级权限服务：加载 conf/row_access_matrix.yaml，按 (role, department) 过滤候选 category。

配置 Schema：
    {"(role, dept)": [category list]}

- JWT 缺失（匿名）→ 只放行 ``KB_ANONYMOUS_CATEGORIES`` 白名单（默认 regulation），
  受限类别（script/case/other）一律不返回（T21，替代 V1 原样放行）。
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

from app.settings import settings

logger = structlog.get_logger()

# 默认权限矩阵路径：services/rag-service/conf/row_access_matrix.yaml
_DEFAULT_MATRIX_PATH = (
    Path(__file__).resolve().parent.parent.parent / "conf" / "row_access_matrix.yaml"
)

# 保底全量放行的 4 类 category（V1 兼容）
_ALL_CATEGORIES = {"regulation", "script", "case", "other"}

# 匿名默认可见类别：仅公开发布的制度文本（T21）
_DEFAULT_ANONYMOUS_CATEGORIES = "regulation"


def anonymous_allowed_categories() -> set[str]:
    """匿名（无身份）请求允许访问的 category 白名单。

    读取 ``KB_ANONYMOUS_CATEGORIES``（逗号分隔，默认 ``regulation``），
    仅保留合法类别；空配置或全部非法 → 空集合（fail-closed，匿名零结果）。
    """
    raw = getattr(settings, "KB_ANONYMOUS_CATEGORIES", _DEFAULT_ANONYMOUS_CATEGORIES)
    if isinstance(raw, (list, tuple, set)):
        values = [str(v).strip() for v in raw]
    else:
        values = [v.strip() for v in str(raw).split(",")]
    return {v for v in values if v in _ALL_CATEGORIES}


def filter_by_categories(
    items: list[dict], allowed: set[str], category_filter: str | None = None
) -> list[dict]:
    """按 allowed 类别集合过滤 items；category_filter 非空时先取交集。"""
    if category_filter is not None:
        allowed = allowed & {category_filter}
    return [item for item in items if item.get("category") in allowed]


class RowAccessService:
    """行级权限服务：按 JWT 的 role/department 过滤候选 chunk 的 category。"""

    def __init__(self, path: str | Path | None = None):
        self._matrix: dict[tuple, list[str]] = {}
        self._has_three_tuple: bool = False
        # T67：矩阵加载失败标志——fail-closed，查找降级为匿名白名单而非全量放行
        self._degraded: bool = False
        self.load(path or _DEFAULT_MATRIX_PATH)

    def load(self, path: str | Path) -> None:
        """读取行级权限矩阵 YAML。

        Schema: ``{"(role, dept)": [category list]}``，键解析为 ``(role, dept)`` 元组。
        支持三元组键 ``(tenant_id, role, dept)``（V2 tenant_id bypass）。

        T67：文件缺失/解析失败/空矩阵 → fail-closed 降级——登录态查找降为
        ``KB_ANONYMOUS_CATEGORIES`` 白名单（权限不放大）；每次加载失败均记
        error（不允许记一次后静默）。
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
            logger.error("row_access_matrix_empty", path=str(p))
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
        self._degraded = False
        logger.info(
            "row_access_matrix_loaded",
            path=str(p),
            entries=len(matrix),
            has_three_tuple=has_three,
        )

    def _reset(self) -> None:
        """置空矩阵并标记降级（T67 fail-closed）：查找降级为匿名白名单。"""
        self._matrix = {}
        self._has_three_tuple = False
        self._degraded = True

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

        T67：矩阵失效（加载失败/为空）时降级为 ``KB_ANONYMOUS_CATEGORIES``
        白名单（fail-closed，权限不放大）；矩阵正常时保持既有优先级链。
        """
        if self._degraded:
            return anonymous_allowed_categories()
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
            jwt_payload: JWT 载荷；None（匿名）时仅放行
                ``KB_ANONYMOUS_CATEGORIES`` 白名单（默认 regulation）。
            category_filter: 可选类别过滤，与 allowed 集合取交集。

        Returns:
            过滤后的 items 列表；空结果合法，不抛 403。
        """
        if jwt_payload is None:
            # T21：匿名请求不再放行受限文档，仅可见公开类别白名单
            allowed = anonymous_allowed_categories()
            result = filter_by_categories(items, allowed, category_filter)
            logger.info(
                "row_access_anonymous_filtered",
                allowed=sorted(allowed),
                category_filter=category_filter,
                before=len(items),
                after=len(result),
            )
            return result

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
        result = filter_by_categories(items, allowed, category_filter)
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
