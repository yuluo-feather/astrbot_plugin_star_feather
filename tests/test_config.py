"""配置读取工具单测：分组优先 / 扁平回退 / None 兜底 / int 下限。
——配置乱来？牌灵宁愿给默认值也不炸。"""
from config import _cfg_bool, _cfg_get, _cfg_int


class TestCfgGet:
    def test_group_wins_over_flat(self):
        assert _cfg_get({"ai": {"ai_timeout": 5}, "ai_timeout": 99}, "ai", "ai_timeout", 30) == 5

    def test_flat_fallback(self):
        assert _cfg_get({"ai_timeout": 7}, "ai", "ai_timeout", 30) == 7

    def test_none_config_returns_default(self):
        assert _cfg_get(None, "ai", "ai_timeout", 30) == 30

    def test_group_not_dict_falls_back(self):
        assert _cfg_get({"ai": "oops"}, "ai", "ai_timeout", 30) == 30

    def test_none_value_returns_default(self):
        assert _cfg_get({"ai": {"ai_timeout": None}}, "ai", "ai_timeout", 30) == 30
        assert _cfg_get({"ai_timeout": None}, "ai", "ai_timeout", 30) == 30


class TestCfgInt:
    def test_int_value(self):
        assert _cfg_int({"a": {"x": 5}}, "a", "x", 3) == 5

    def test_flat_fallback(self):
        assert _cfg_int({"x": "7"}, "a", "x", 3) == 7

    def test_invalid_returns_default(self):
        assert _cfg_int({"a": {"x": "bad"}}, "a", "x", 3) == 3
        assert _cfg_int({"a": {"x": None}}, "a", "x", 3) == 3

    def test_floor_applied(self):
        assert _cfg_int({"a": {"x": 1}}, "a", "x", 30, floor=5) == 5

    def test_no_floor_passes_through(self):
        assert _cfg_int({"a": {"x": 0}}, "a", "x", 30) == 0


class TestCfgBool:
    def test_bool_value(self):
        assert _cfg_bool({"a": {"x": False}}, "a", "x", True) is False
        assert _cfg_bool({"a": {"x": True}}, "a", "x", False) is True

    def test_string_false_not_true(self):
        # 回归：bool("false") 曾错判为 True
        assert _cfg_bool({"a": {"x": "false"}}, "a", "x", True) is False
        assert _cfg_bool({"a": {"x": "0"}}, "a", "x", True) is False
        assert _cfg_bool({"a": {"x": "off"}}, "a", "x", True) is False

    def test_string_true_forms(self):
        assert _cfg_bool({"a": {"x": "true"}}, "a", "x", False) is True
        assert _cfg_bool({"a": {"x": "1"}}, "a", "x", False) is True

    def test_numeric_zero_false(self):
        assert _cfg_bool({"a": {"x": 0}}, "a", "x", True) is False
        assert _cfg_bool({"a": {"x": 1}}, "a", "x", False) is True

    def test_default_when_missing(self):
        assert _cfg_bool({}, "a", "x", True) is True


