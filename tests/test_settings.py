"""配置语义层单测：send_mode 三态 / 旧配置迁移 / TarotSettings 解析。
——默认值都在这，别处别自作主张。"""
from settings import (
    DEFAULT_AI_PERSONA,
    TarotSettings,
    resolve_send_mode,
)


class TestResolveSendMode:
    """send_mode：新 key 优先，旧 forward_result / show_image 自动迁移。"""

    def test_new_mode_wins(self):
        assert resolve_send_mode({"output": {"send_mode": "forward"}}) == "forward"

    def test_new_mode_beats_legacy(self):
        # 面板已换新但旧字段残留：新 key 优先，不被旧值覆盖
        assert resolve_send_mode({"output": {"send_mode": "text_only", "forward_result": True}}) == "text_only"

    def test_legacy_forward_wins(self):
        # forward_result=true → forward（忽略图开关，转发即含图）
        assert resolve_send_mode({"output": {"forward_result": True, "show_image": False}}) == "forward"

    def test_legacy_show_false_maps_text_only(self):
        assert resolve_send_mode({"show_image": False}) == "text_only"

    def test_flat_legacy_forward(self):
        assert resolve_send_mode({"forward_result": True}) == "forward"

    def test_defaults_forward(self):
        assert resolve_send_mode(None) == "forward"

    def test_invalid_falls_back_forward(self):
        assert resolve_send_mode({"output": {"send_mode": "weird"}}) == "forward"


class TestDailyCard:
    """output.daily_card：今日牌运卡海报开关（关闭 → 回退普通牌面图）。"""

    def test_default_true(self):
        assert TarotSettings(None).daily_card is True

    def test_off_false(self):
        assert TarotSettings({"output": {"daily_card": False}}).daily_card is False

    def test_flat_legacy(self):
        assert TarotSettings({"daily_card": False}).daily_card is False


class TestAiPersona:
    """ai.persona：五态枚举（off/tsundere/gentle/mystic/random），非法值回退默认。"""

    def test_default_random(self):
        assert DEFAULT_AI_PERSONA == "random"
        assert TarotSettings(None).ai_persona == "random"

    def test_valid_values(self):
        for v in ("off", "tsundere", "gentle", "mystic", "random"):
            assert TarotSettings({"ai": {"persona": v}}).ai_persona == v

    def test_invalid_falls_back_random(self):
        assert TarotSettings({"ai": {"persona": "evil"}}).ai_persona == "random"
        assert TarotSettings({"ai": {"persona": ""}}).ai_persona == "random"
        assert TarotSettings({"ai": {"persona": 123}}).ai_persona == "random"

    def test_flat_legacy(self):
        # 旧扁平配置兼容：顶层 persona 键（config 扁平回退规则）
        assert TarotSettings({"persona": "mystic"}).ai_persona == "mystic"


class TestTarotSettings:
    def test_grouped_values(self):
        cfg = {"ai": {"ai_timeout": 7, "enable_ai": False, "question_max_len": 0},
               "output": {"send_mode": "plain", "disclaimer": "x", "daily_fixed": False},
               "limit": {"daily_count": 3},
               "tool": {"llm_tool_enabled": False}}
        s = TarotSettings(cfg)
        assert s.ai_timeout == 7 and s.enable_ai is False
        assert s.send_mode == "plain" and s.ai_max_len == 0
        assert s.daily_count_limit == 3 and s.disclaimer == "x" and s.daily_fixed is False
        assert s.llm_tool_enabled is False

    def test_legacy_flat_still_loads(self):
        # 升级前扁平配置：分组缺失时回退顶层读取
        s = TarotSettings({"show_image": False, "forward_result": True})
        assert s.send_mode == "forward"
        assert TarotSettings({"ai_timeout": 9}).ai_timeout == 9
