"""配置语义层单测：send_mode 三态 / 旧配置迁移 / Settings 解析。
——默认值都在这，别处别自作主张。"""
from data.plugins.astrbot_plugin_star_feather.core.settings import (
    DEFAULT_AI_PERSONA,
    parse,
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
        assert parse(None).output.daily_card is True

    def test_off_false(self):
        assert parse({"output": {"daily_card": False}}).output.daily_card is False

    def test_flat_legacy(self):
        assert parse({"daily_card": False}).output.daily_card is False


class TestAiPersona:
    """ai.persona：五态枚举（off/tsundere/gentle/mystic/random），非法值回退默认。"""

    def test_default_random(self):
        assert DEFAULT_AI_PERSONA == "random"
        assert parse(None).ai.persona == "random"

    def test_valid_values(self):
        for v in ("off", "tsundere", "gentle", "mystic", "random"):
            assert parse({"ai": {"persona": v}}).ai.persona == v

    def test_invalid_falls_back_random(self):
        assert parse({"ai": {"persona": "evil"}}).ai.persona == "random"
        assert parse({"ai": {"persona": ""}}).ai.persona == "random"
        assert parse({"ai": {"persona": 123}}).ai.persona == "random"

    def test_flat_legacy(self):
        # 旧扁平配置兼容：顶层 persona 键（config 扁平回退规则）
        assert parse({"persona": "mystic"}).ai.persona == "mystic"


class TestSpiritLine:
    """牌灵的话开关（ai.spirit_line）：默认开，显式 false 才关。"""

    def test_default_on(self):
        assert parse(None).ai.spirit_line is True

    def test_off(self):
        assert parse({"ai": {"spirit_line": False}}).ai.spirit_line is False

    def test_flat_legacy(self):
        assert parse({"spirit_line": False}).ai.spirit_line is False


class TestParsedSettings:
    def test_grouped_values(self):
        cfg = {"ai": {"ai_timeout": 7, "enable_ai": False, "question_max_len": 0,
                      "parallel_stages": False},
               "output": {"send_mode": "plain", "disclaimer": "x", "daily_fixed": False},
               "limit": {"daily_count": 3},
               "tool": {"llm_tool_enabled": False}}
        s = parse(cfg)
        assert s.ai.timeout == 7 and s.ai.enable_ai is False
        assert s.output.send_mode == "plain" and s.ai.question_max_len == 0
        assert s.limit.daily_count == 3 and s.output.disclaimer == "x"
        assert s.output.daily_fixed is False
        assert s.tool.llm_enabled is False
        assert s.ai.parallel_stages is False

    def test_parallel_stages_defaults_on(self):
        """两段 AI 默认并发（ai.parallel_stages=true），关掉才顺序执行。"""
        from data.plugins.astrbot_plugin_star_feather.core.settings import (
            DEFAULT_AI_PARALLEL_STAGES,
        )
        assert parse(None).ai.parallel_stages is DEFAULT_AI_PARALLEL_STAGES

    def test_negative_question_max_len_falls_back_to_default(self):
        """负数不是「更不压缩」：坏值回默认，只有 0 才表示明确关闭截断。"""
        from data.plugins.astrbot_plugin_star_feather.core.settings import (
            DEFAULT_QUESTION_MAX_LEN,
        )
        assert parse({"ai": {"question_max_len": -5}}).ai.question_max_len == DEFAULT_QUESTION_MAX_LEN
        # -0.5 会被 int() 截成 0，不能当成「明确关闭截断」
        assert parse({"ai": {"question_max_len": -0.5}}).ai.question_max_len == DEFAULT_QUESTION_MAX_LEN
        # YAML 的 .inf / -.inf 在 int() 处抛 OverflowError，不能带走插件加载
        assert parse({"ai": {"question_max_len": float("inf")}}).ai.question_max_len == DEFAULT_QUESTION_MAX_LEN
        assert parse({"ai": {"question_max_len": float("-inf")}}).ai.question_max_len == DEFAULT_QUESTION_MAX_LEN
        assert parse({"ai": {"question_max_len": 120}}).ai.question_max_len == 120

    def test_ai_timeout_semantics_zero_unlimited_others_floored(self):
        """0 = 不限制单个请求；**非 0 取值一律不低于 5 秒**（2026-10-14 主人定）。"""
        assert parse({"ai": {"ai_timeout": 0}}).ai.timeout == 0
        assert parse({"ai": {"ai_timeout": 3}}).ai.timeout == 5
        assert parse({"ai": {"ai_timeout": -3}}).ai.timeout == 5
        assert parse({"ai": {"ai_timeout": 5}}).ai.timeout == 5
        assert parse({"ai": {"ai_timeout": 30}}).ai.timeout == 30

    def test_ai_budget_only_explicit_zero_means_unlimited(self):
        """只有显式 0 = 不限制；负数/非数字回退默认值（-10 不许被夹成 0）。"""
        from data.plugins.astrbot_plugin_star_feather.core.settings import (
            DEFAULT_AI_BUDGET,
        )
        assert parse({"ai": {"ai_budget_seconds": 0}}).ai.budget_seconds == 0
        assert parse({"ai": {"ai_budget_seconds": -10}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        assert parse({"ai": {"ai_budget_seconds": "oops"}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        assert parse({"ai": {"ai_budget_seconds": 300}}).ai.budget_seconds == 300
        # 小数不能被截成 0（0.5 秒 -> 至少 1 秒），布尔 / inf 一律回退默认值
        assert parse({"ai": {"ai_budget_seconds": 0.5}}).ai.budget_seconds == 1
        assert parse({"ai": {"ai_budget_seconds": True}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        assert parse({"ai": {"ai_budget_seconds": float("inf")}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        # 超大整数（float 溢出）与下溢成 -0.0 的极小负数都不许变成「不限制」
        assert parse({"ai": {"ai_budget_seconds": 10 ** 400}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        assert parse({"ai": {"ai_budget_seconds": "-1e-9999"}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        assert parse({"ai": {"ai_budget_seconds": -0.0}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        # 下溢成 +0.0 的极小正数同样是坏值：它不是「显式 0」
        assert parse({"ai": {"ai_budget_seconds": "1e-9999"}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        # 有限但大到算不出截止点的值（1e308）回退默认；上限本身仍可用
        assert parse({"ai": {"ai_budget_seconds": "1e308"}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        assert parse({"ai": {"ai_budget_seconds": 24 * 3600 + 1}}).ai.budget_seconds == DEFAULT_AI_BUDGET
        assert parse({"ai": {"ai_budget_seconds": 24 * 3600}}).ai.budget_seconds == 24 * 3600

    def test_ai_concurrency_and_fallback_defaults(self):
        from data.plugins.astrbot_plugin_star_feather.core.settings import (
            DEFAULT_AI_MAX_CONCURRENCY,
            DEFAULT_ALL_PROVIDERS_FALLBACK,
        )
        s = parse(None)
        assert s.ai.max_concurrency == DEFAULT_AI_MAX_CONCURRENCY == 2
        assert s.ai.all_providers_fallback is DEFAULT_ALL_PROVIDERS_FALLBACK is False
        assert parse({"ai": {"max_concurrency": 0}}).ai.max_concurrency == 0
        assert parse({"ai": {"max_concurrency": 5}}).ai.max_concurrency == 5
        assert parse({"ai": {"max_concurrency": -3}}).ai.max_concurrency == 2
        assert parse({"ai": {"all_providers_fallback": True}}).ai.all_providers_fallback is True

    def test_negative_cooldown_and_limit_values_fall_back_to_defaults(self):
        """负数不是「另一种关闭」：只有显式 0 才关闭，负数回默认值。"""
        from data.plugins.astrbot_plugin_star_feather.core.settings import (
            DEFAULT_AI_COOLDOWN,
            DEFAULT_CMD_RATE_LIMIT,
            DEFAULT_DAILY_COUNT,
            DEFAULT_TOOL_COOLDOWN,
        )
        assert parse({"ai": {"ai_fail_cooldown": 0}}).ai.fail_cooldown == 0
        assert parse({"ai": {"ai_fail_cooldown": -5}}).ai.fail_cooldown == DEFAULT_AI_COOLDOWN
        # -0.5 取整后变 0，不能当成显式关闭
        assert parse({"ai": {"ai_fail_cooldown": -0.5}}).ai.fail_cooldown == DEFAULT_AI_COOLDOWN
        assert parse({"tool": {"llm_tool_cooldown": 0}}).tool.llm_cooldown == 0
        assert parse({"tool": {"llm_tool_cooldown": -5}}).tool.llm_cooldown == DEFAULT_TOOL_COOLDOWN
        assert parse({"tool": {"cmd_rate_limit": 0}}).tool.cmd_rate_limit == 0
        assert parse({"tool": {"cmd_rate_limit": -7}}).tool.cmd_rate_limit == DEFAULT_CMD_RATE_LIMIT
        assert parse({"limit": {"daily_count": 0}}).limit.daily_count == 0
        assert parse({"limit": {"daily_count": -3}}).limit.daily_count == DEFAULT_DAILY_COUNT
        # 正常值与扁平旧配置照旧
        s = parse({"ai_fail_cooldown": 12, "tool": {"cmd_rate_limit": 2}})
        assert s.ai.fail_cooldown == 12 and s.tool.cmd_rate_limit == 2

    def test_legacy_flat_still_loads(self):
        # 升级前扁平配置：分组缺失时回退顶层读取
        s = parse({"show_image": False, "forward_result": True})
        assert s.output.send_mode == "forward"
        assert parse({"ai_timeout": 9}).ai.timeout == 9


class TestSettingsDataclass:
    """冻结的嵌套 Settings：默认值只在这里。"""

    def test_defaults_are_grouped_and_frozen(self):
        import dataclasses

        import pytest
        from data.plugins.astrbot_plugin_star_feather.core.settings import (
            DEFAULT_AI_BUDGET,
            DEFAULT_AI_TIMEOUT,
            DEFAULT_DAILY_COUNT,
            DEFAULT_SEND_MODE,
            Settings,
        )
        s = parse(None)
        assert isinstance(s, Settings)
        assert s.ai.timeout == DEFAULT_AI_TIMEOUT
        assert s.ai.budget_seconds == DEFAULT_AI_BUDGET
        assert s.ai.persona == DEFAULT_AI_PERSONA
        assert s.limit.daily_count == DEFAULT_DAILY_COUNT
        assert s.output.send_mode == DEFAULT_SEND_MODE
        assert s.tool.llm_enabled is True
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.ai.timeout = 1

    def test_parse_nested_values(self):
        cfg = {"ai": {"ai_timeout": 7, "parallel_stages": False, "ai_budget_seconds": 60},
               "tool": {"llm_tool_cooldown": 5},
               "limit": {"daily_count": 3},
               "output": {"send_mode": "plain", "segment_size": 120}}
        s = parse(cfg)
        assert (s.ai.timeout, s.ai.parallel_stages, s.ai.budget_seconds) == (7, False, 60)
        assert s.tool.llm_cooldown == 5
        assert s.limit.daily_count == 3
        assert (s.output.send_mode, s.output.segment_size) == ("plain", 120)
