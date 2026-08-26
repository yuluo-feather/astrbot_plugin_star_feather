"""字体子系统单测：内置子集覆盖、缺字检测、回退链与缓存污染回归。
——想写得好看，先过字体这一关。"""
import os

import fonts
from PIL import ImageFont
from tarot_data import TAROT_CARDS


class TestFont:
    def _load_builtin(self, bold=False):
        path = os.path.join(fonts._FONT_DIR, "StarFeather-Regular.otf")
        return ImageFont.truetype(path, 20)

    def test_current_texts_covered(self):
        # 验证 README 声明：当前牌库文案（牌名+正逆位牌义）全部在内置子集内
        font = self._load_builtin()
        texts = [cn for _, _, cn, *_ in TAROT_CARDS]
        texts += [up for _, _, _, _, up, _ in TAROT_CARDS]
        texts += [down for _, _, _, _, _, down in TAROT_CARDS]
        texts += ["牌阵 · 羽时三刻", "正位", "逆位", "过去", "现在", "未来", "【过去】"]
        for t in texts:
            assert fonts._font_covers(font, t), f"子集缺字: {t!r}"

    def test_missing_glyph_detected(self):
        font = self._load_builtin()
        assert not fonts._font_covers(font, "𠀀")  # U+20000 不在子集

    def test_load_font_falls_back_for_missing_glyph(self):
        # 缺字文本应回退到能覆盖的系统字体（Windows 开发机必有微软雅黑）
        f = fonts._load_font(20, text="𠀀测试")
        assert f is not None
        if os.name == "nt":
            assert "StarFeather" not in (getattr(f, "path", "") or "")

    def test_load_font_keeps_builtin_when_covered(self):
        f = fonts._load_font(20, text="权杖王后正位")
        assert "StarFeather" in (getattr(f, "path", "") or "")

    def test_text_select_does_not_pollute_cache(self):
        # 回归：带生僻字文本的选择（回退系统字体）不得写入缓存，
        # 否则之后同 size 的常用字渲染会错误沿用系统字体、丢失内置字体
        fonts._FONT_CACHE.clear()
        rare = fonts._load_font(20, text="𠀀测试")
        assert rare is not None
        # 缓存此时应为空（或未含该 size 键）——关键断言
        assert (20, False) not in fonts._FONT_CACHE
        # 下一次常用字渲染仍应命中内置字体
        common = fonts._load_font(20, text="权杖王后正位")
        assert "StarFeather" in (getattr(common, "path", "") or "")
