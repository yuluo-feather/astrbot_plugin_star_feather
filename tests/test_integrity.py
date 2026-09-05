"""数据完整性域：牌库 / 签文池 / 字形覆盖 / 配置枚举——枚举性校验都归这里。

说人话：牌灵的家底要定期清点——一张牌、一句话、一个字、一个配置项，都不能少。"""
import os

from PIL import ImageFont

import fonts
from dailylines import SIGIL_LINES
from settings import PERSONA_VALUES, TarotSettings
from tarot_data import TAROT_CARDS


class TestTarotDataIntegrity:
    """牌库完整性：78 张牌，键（花色, 序号）唯一，正逆位是一张牌的两面。"""

    def test_every_card_unique_key(self):
        keys = [(c[0], c[1]) for c in TAROT_CARDS]
        assert len(keys) == 78
        assert len(set(keys)) == 78


class TestDailylinesIntegrity:
    """签文池完整性：78 张全覆盖 / 正逆位各≥2 条 / 无孤儿键 / 正逆不重复 / 长度可控。"""

    def test_every_card_covered(self):
        # 78 张牌每张都必须有签文，正逆各至少 2 条
        keys = {(c[0], c[1]) for c in TAROT_CARDS}
        assert len(TAROT_CARDS) == 78
        assert keys == set(SIGIL_LINES.keys())
        for entry in SIGIL_LINES.values():
            assert len(entry["up"]) >= 2
            assert len(entry["down"]) >= 2

    def test_no_orphan_entries(self):
        # 池里不能有牌库之外的键（写错牌名=白写还查不出）
        keys = {(c[0], c[1]) for c in TAROT_CARDS}
        assert all(k in keys for k in SIGIL_LINES)

    def test_up_down_disjoint(self):
        # 正逆位签文内容不能重复（同一天正逆位撞成同一句就尴尬了）
        for entry in SIGIL_LINES.values():
            assert set(entry["up"]).isdisjoint(entry["down"])

    def test_lines_not_too_long(self):
        # 卡片签文区一行放得下：30 字以内（超出会截断，观感差）
        for entry in SIGIL_LINES.values():
            for line in entry["up"] + entry["down"]:
                assert len(line) <= 32, line


class TestFontIntegrity:
    """字形覆盖完整性：当前牌库文案（牌名+正逆位牌义）全部在内置子集内。"""

    def test_current_texts_covered(self):
        # 验证 README 声明：当前牌库文案（牌名+正逆位牌义）全部在内置子集内
        path = os.path.join(fonts._FONT_DIR, "StarFeather-Regular.otf")
        font = ImageFont.truetype(path, 20)
        texts = [cn for _, _, cn, *_ in TAROT_CARDS]
        texts += [up for _, _, _, _, up, _ in TAROT_CARDS]
        texts += [down for _, _, _, _, _, down in TAROT_CARDS]
        texts += ["牌阵 · 羽时三刻", "正位", "逆位", "过去", "现在", "未来", "【过去】"]
        for t in texts:
            assert fonts._font_covers(font, t), f"子集缺字: {t!r}"


class TestSchemaIntegrity:
    """配置完整性：枚举导出与默认值，与 settings 语义层逐项对齐。"""

    def test_enum_export(self):
        assert PERSONA_VALUES == ("off", "tsundere", "gentle", "mystic", "random")

    def test_defaults(self):
        s = TarotSettings(None)
        assert s.send_mode == "forward"
        assert s.shuffle_lines is True
        assert s.disclaimer == "✨ 占卜仅供娱乐参考，选择权永远在你手里。"
        assert s.daily_fixed is True
        assert s.enable_ai is True
        assert s.segment_size == 300
        assert s.ai_timeout == 30
        assert s.ai_cooldown == 60
        assert s.llm_tool_enabled is True
        assert s.llm_tool_cooldown == 60
        assert s.cmd_rate_limit == 10
        assert s.daily_count_limit == 0
        assert s.ai_provider_id == ""
        assert s.ai_max_len == 200
