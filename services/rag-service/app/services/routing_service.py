"""意图路由服务：加载 conf/intent_routes.yaml，按关键词命中分类并调整召回权重。

配置 Schema：
    {intent_tag: {keywords: list[str], weights: {category: float}}}

- 命中多个意图时取匹配关键词最长者。
- 无命中 → intent_tag=None，weights 为空（消费方默认 1.0），degraded=False。
- 空文件/文件缺失 → has_routes=False，degraded=True，bypass 全 1.0。
- category_filter 非空时仅返回该类别的权重，其余类别保持 1.0（省略）。
"""
from __future__ import annotations

from pathlib import Path

import structlog
import yaml
from pydantic import BaseModel, Field

logger = structlog.get_logger()

# 默认路由配置路径：services/rag-service/conf/intent_routes.yaml
_DEFAULT_ROUTES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "conf" / "intent_routes.yaml"
)


class RouteResult(BaseModel):
    """意图路由结果。weights 仅包含需调整的类别，消费方对缺失类别默认取 1.0。"""
    intent_tag: str | None = None
    weights: dict[str, float] = Field(default_factory=dict)
    degraded: bool = False


class RoutingService:
    """意图路由服务：加载 YAML 规则并按 query 做意图分类与权重调整。"""

    def __init__(self, path: str | Path | None = None):
        self._routes: dict[str, dict] = {}
        self.has_routes: bool = False
        self.degraded: bool = True
        self.load(path or _DEFAULT_ROUTES_PATH)

    def load(self, path: str | Path) -> None:
        """读取意图路由 YAML 配置。

        Schema: ``{intent_tag: {keywords: list[str], weights: {category: float}}}``。
        空文件/文件缺失/解析失败 → has_routes=False、degraded=True。
        """
        p = Path(path)
        if not p.exists():
            logger.error("routing_config_not_found", path=str(p))
            self._reset_degraded()
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as e:
            logger.error("routing_config_load_failed", path=str(p), error=str(e))
            self._reset_degraded()
            return

        if not data or not isinstance(data, dict):
            # 空文件或非 dict → 降级 bypass
            logger.warning("routing_config_empty", path=str(p))
            self._reset_degraded()
            return

        routes: dict[str, dict] = {}
        for tag, cfg in data.items():
            if not isinstance(cfg, dict) or not isinstance(tag, str):
                continue
            keywords = cfg.get("keywords") or []
            if not isinstance(keywords, list):
                keywords = []
            keywords = [kw for kw in keywords if isinstance(kw, str) and kw]
            raw_weights = cfg.get("weights") or {}
            if not isinstance(raw_weights, dict):
                raw_weights = {}
            weights: dict[str, float] = {}
            for cat, w in raw_weights.items():
                if (
                    isinstance(cat, str)
                    and isinstance(w, (int, float))
                    and not isinstance(w, bool)
                ):
                    weights[cat] = float(w)
            routes[tag] = {"keywords": keywords, "weights": weights}

        self._routes = routes
        self.has_routes = bool(routes)
        self.degraded = not self.has_routes
        logger.info(
            "routing_config_loaded",
            path=str(p),
            routes=len(routes),
            tags=list(routes),
        )

    def _reset_degraded(self) -> None:
        """置空路由并标记降级。"""
        self._routes = {}
        self.has_routes = False
        self.degraded = True

    def classify_and_weight(
        self, query: str, category_filter: str | None = None
    ) -> RouteResult:
        """对 query 做意图分类并返回对应权重。

        - 命中多个意图时取匹配关键词最长者。
        - 无命中 → intent_tag=None，weights 为空，degraded=False。
        - category_filter 非空时仅返回该类别的权重，其余类别保持 1.0（省略）。
        - 未加载路由（degraded）→ 返回 degraded=True，weights 为空。
        """
        if not self.has_routes:
            return RouteResult(intent_tag=None, weights={}, degraded=True)
        if not query:
            return RouteResult(intent_tag=None, weights={}, degraded=False)

        best_tag: str | None = None
        best_len = -1
        for tag, cfg in self._routes.items():
            for kw in cfg["keywords"]:
                if kw in query and len(kw) > best_len:
                    best_len = len(kw)
                    best_tag = tag

        if best_tag is None:
            # 无命中 → 不降级，全 1.0（省略）
            return RouteResult(intent_tag=None, weights={}, degraded=False)

        matched_weights = self._routes[best_tag]["weights"]
        if category_filter is not None:
            # 仅应用指定类别权重，其余保持 1.0
            weights: dict[str, float] = {}
            if category_filter in matched_weights:
                weights[category_filter] = matched_weights[category_filter]
        else:
            weights = dict(matched_weights)

        logger.info(
            "routing_matched",
            intent_tag=best_tag,
            keyword_len=best_len,
            category_filter=category_filter,
            weights=weights,
        )
        return RouteResult(intent_tag=best_tag, weights=weights, degraded=False)
