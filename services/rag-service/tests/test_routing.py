"""意图路由服务单元测试（§8.6 / §16.4）。

覆盖：
- 命中 negotiate → regulation×1.8 script×1.2
- 空配置 bypass degraded
- 多关键词命中选最长
- SIGHUP 信号触发 reload
"""


from app.services.routing_service import RoutingService


class TestRoutingClassify:
    def test_negotiate_keyword_weights(self):
        """命中"协商"→ negotiate 意图，weights 含 regulation:1.8 script:1.2。"""
        svc = RoutingService()  # 加载默认 conf/intent_routes.yaml
        assert svc.has_routes is True
        result = svc.classify_and_weight("我想协商还款方案")
        assert result.intent_tag == "negotiate"
        assert result.degraded is False
        assert result.weights["regulation"] == 1.8
        assert result.weights["script"] == 1.2
        assert result.weights["case"] == 1.0
        assert result.weights["other"] == 0.6

    def test_no_match_returns_empty_weights(self):
        """无关键词命中 → intent_tag=None, weights 为空, degraded=False。"""
        svc = RoutingService()
        result = svc.classify_and_weight("今天天气不错")
        assert result.intent_tag is None
        assert result.weights == {}
        assert result.degraded is False

    def test_multiple_keywords_picks_longest(self, tmp_path):
        """多关键词命中选最长匹配关键词所属意图。"""
        cfg = tmp_path / "routes.yaml"
        cfg.write_text(
            "intent_a:\n"
            "  keywords: ['还']\n"
            "  weights: {regulation: 1.1}\n"
            "intent_b:\n"
            "  keywords: ['协商还款']\n"
            "  weights: {regulation: 2.2}\n",
            encoding="utf-8",
        )
        svc = RoutingService(path=cfg)
        # "协商还款"（4 字）> "还"（1 字）→ intent_b
        result = svc.classify_and_weight("我要协商还款")
        assert result.intent_tag == "intent_b"
        assert result.weights["regulation"] == 2.2

    def test_category_filter_restricts_weights(self):
        """category_filter 非空时仅返回该类别权重。"""
        svc = RoutingService()
        result = svc.classify_and_weight("协商分期", category_filter="regulation")
        assert result.intent_tag == "negotiate"
        # 仅 regulation 权重，其余省略（消费方默认 1.0）
        assert result.weights == {"regulation": 1.8}


class TestRoutingDegraded:
    def test_empty_config_degraded(self, tmp_path):
        """空配置文件 → has_routes=False, degraded=True。"""
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("", encoding="utf-8")
        svc = RoutingService(path=cfg)
        assert svc.has_routes is False
        assert svc.degraded is True
        result = svc.classify_and_weight("协商还款")
        assert result.degraded is True
        assert result.weights == {}

    def test_missing_config_degraded(self, tmp_path):
        """文件缺失 → degraded=True。"""
        svc = RoutingService(path=tmp_path / "nonexistent.yaml")
        assert svc.has_routes is False
        assert svc.degraded is True


class TestRoutingSighupReload:
    def test_sighup_triggers_reload(self, tmp_path, monkeypatch):
        """SIGHUP 信号触发 RoutingService.load 重新加载配置。

        通过直接调用 load 方法模拟 reload 行为（不依赖进程信号），
        验证配置变更后 classify_and_weight 能识别新意图。
        """
        cfg = tmp_path / "routes.yaml"
        cfg.write_text(
            "initial_intent:\n"
            "  keywords: ['初始']\n"
            "  weights: {regulation: 1.0}\n",
            encoding="utf-8",
        )
        svc = RoutingService(path=cfg)
        assert svc.classify_and_weight("初始查询").intent_tag == "initial_intent"

        # 模拟 SIGHUP 后配置变更
        cfg.write_text(
            "reloaded_intent:\n"
            "  keywords: ['重载']\n"
            "  weights: {regulation: 2.0}\n",
            encoding="utf-8",
        )
        svc.load(cfg)  # 等价于 SIGHUP handler 调用 routing_service.load(conf)
        assert svc.classify_and_weight("重载查询").intent_tag == "reloaded_intent"
        # 旧意图不再命中
        assert svc.classify_and_weight("初始查询").intent_tag is None
