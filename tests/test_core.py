"""核心纯逻辑单测：选阵 / 别名剥离 / 分段 / 抽牌 / 字体覆盖与渲染冒烟。"""
import os

import pytest
from PIL import ImageFont

import card_render
from main import StarTarot
from tarot_data import TAROT_CARDS

T = StarTarot.__new__(StarTarot)  # 绕过 __init__（不依赖 Context）
T.segment_size = 300              # 与 DEFAULT_SEGMENT_SIZE 对齐，供 _split_sections 兜底


# ---------- 选阵 _select_formation ----------
class TestSelectFormation:
    def test_explicit_alias_wins(self):
        assert T._select_formation("圣三角 考研如何") == "羽镜"
        assert T._select_formation("恋人十字 我们还有希望吗") == "恋羽十字"
        assert T._select_formation("时间之流 看看明年") == "羽时三刻"

    def test_keyword_weight(self):
        assert T._select_formation("我和她感情如何") == "恋羽十字"
        assert T._select_formation("考研 复习计划") == "羽镜"
        assert T._select_formation("下个月运势如何") == "羽时三刻"

    def test_keyword_tie_keeps_map_order(self):
        # 情感组与事业组各命中 1 词：平局时按 KEYWORD_MAP 顺序取先
        assert T._select_formation("感情 工作") == "恋羽十字"

    def test_content_inference(self):
        assert T._select_formation("他会不会来") == "恋羽十字"
        assert T._select_formation("我们合适吗") == "恋羽十字"

    def test_fallback(self):
        assert T._select_formation("今天天气不错") == "羽时三刻"
        assert T._select_formation("") == "羽时三刻"

    def test_blank_is_safe(self):
        assert T._select_formation(None) == "羽时三刻"


# ---------- 别名剥离 _strip_alias ----------
class TestStripAlias:
    def test_standalone_alias_removed(self):
        assert T._strip_alias("圣三角 考研如何") == "考研如何"

    def test_verb_usage_kept(self):
        # 「单抽一张」中单抽作动词（前为「帮」字非边界），不应剥离
        assert T._strip_alias("帮我单抽一张牌") == "帮我单抽一张牌"

    def test_standalone_single_removed(self):
        assert T._strip_alias("单抽") == ""

    def test_punctuation_stripped(self):
        assert T._strip_alias("圣三角，考研如何？") == "考研如何"

    def test_blank_safe(self):
        assert T._strip_alias(None) == ""


# ---------- 长度分段 _split_text ----------
class TestSplitText:
    def test_hard_cut_join_reversible(self):
        text = "abcdefghij"
        parts = T._split_text(text, 5)
        assert len(parts) == 2
        assert "".join(parts) == text

    def test_newline_preferred(self):
        text = "第一行子\n第二行子"  # 9 字符 > size 7，触发切分
        parts = T._split_text(text, 7)
        assert parts[0].endswith("\n")
        assert "".join(parts) == text

    def test_short_text_single_part(self):
        assert T._split_text("短句", 300) == ["短句"]

    def test_empty(self):
        assert T._split_text("", 10) == []
        assert T._split_text(None, 10) == []


# ---------- 标记分段 _split_sections ----------
class TestSplitSections:
    def test_marked_sections(self):
        text = "【第1张·过去】一【第2张·现在】二【总结】三"
        parts = T._split_sections(text)
        assert len(parts) == 3
        assert parts[0].startswith("【第1张·过去】")
        assert parts[2].startswith("【总结】")

    def test_less_than_two_marks_falls_back(self):
        # 只有一个标记 → 不足以切分，走长度兜底
        text = "【总结】一句话"
        assert T._split_sections(text) == T._split_text(text, T.segment_size)

    def test_empty(self):
        assert T._split_sections("") == []


# ---------- 抽牌与信息拾取 ----------
class TestDraw:
    def test_count_matches_formation(self):
        positions, picks = T._draw("羽镜")
        assert len(picks) == len(positions) == 3

    def test_no_repeat(self):
        _, picks = T._draw("恋羽十字")
        cards = [p["card"] for p in picks]
        assert len(set(cards)) == len(cards)

    def test_upright_is_bool(self):
        _, picks = T._draw("羽签")
        assert isinstance(picks[0]["upright"], bool)

    def test_pick_info_orientation(self):
        pick = {"card": ("wands", "13", "权杖王后", "Queen of Wands",
                         "自信魅力", "嫉妒占有"), "upright": False}
        suit, cn, state, meaning = T._pick_info(pick)
        assert (suit, cn, state, meaning) == ("wands", "权杖王后", "逆位", "嫉妒占有")


# ---------- 字体覆盖与回退 ----------
class TestFont:
    def _load_builtin(self, bold=False):
        path = os.path.join(card_render._FONT_DIR,
                            "StarFeather-Bold.otf" if bold else "StarFeather-Regular.otf")
        return ImageFont.truetype(path, 20)

    def test_current_texts_covered(self):
        # 验证 README 声明：当前牌库文案（牌名+正逆位牌义）全部在内置子集内
        font = self._load_builtin()
        texts = [cn for _, _, cn, *_ in TAROT_CARDS]
        texts += [up for _, _, _, _, up, _ in TAROT_CARDS]
        texts += [down for _, _, _, _, _, down in TAROT_CARDS]
        texts += ["牌阵 · 羽时三刻", "正位", "逆位", "过去", "现在", "未来", "【过去】"]
        for t in texts:
            assert card_render._font_covers(font, t), f"子集缺字: {t!r}"

    def test_missing_glyph_detected(self):
        font = self._load_builtin()
        assert not card_render._font_covers(font, "𠀀")  # U+20000 不在子集

    def test_load_font_falls_back_for_missing_glyph(self):
        # 缺字文本应回退到能覆盖的系统字体（Windows 开发机必有微软雅黑）
        f = card_render._load_font(20, text="𠀀测试")
        assert f is not None
        if os.name == "nt":
            assert "StarFeather" not in (getattr(f, "path", "") or "")

    def test_load_font_keeps_builtin_when_covered(self):
        f = card_render._load_font(20, text="权杖王后正位")
        assert "StarFeather" in (getattr(f, "path", "") or "")


# ---------- 渲染冒烟 ----------
class TestRenderSmoke:
    @pytest.fixture(autouse=True)
    def _reset_font_cache(self):
        card_render._FONT_CACHE.clear()
        yield

    def test_render_cards_produces_png(self, tmp_path):
        positions = ["过去", "现在", "未来"]
        picks = [
            {"card": ("wands", "13", "权杖王后", "Queen of Wands",
                      "自信魅力", "嫉妒占有"), "upright": True},
            {"card": ("cups", "2", "圣杯二", "Two of Cups",
                      "两情相悦", "关系失衡"), "upright": False},
            {"card": ("major", "19", "太阳", "The Sun",
                      "成功喜悦", "乌云遮日"), "upright": True},
        ]
        path = card_render.render_cards(positions, picks, "羽时三刻", save_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert path.endswith(".png") and os.path.getsize(path) > 10_000
