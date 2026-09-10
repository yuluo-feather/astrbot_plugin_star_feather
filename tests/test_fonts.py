"""字体子系统单测：缺字检测、回退链与缓存污染回归（字形覆盖完整性在 test_integrity）。
——想写得好看，先过字体这一关。"""
import os

from PIL import ImageFont

import fonts


class TestFont:
    def _load_builtin(self, bold=False):
        path = os.path.join(fonts._FONT_DIR, "StarFeather-Regular.otf")
        return ImageFont.truetype(path, 20)

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

    def test_unparsable_result_is_cached_as_negative(self, monkeypatch):
        """无法解析的字体路径：None 也要入缓存（键存在即已判定），不重复重算。

        旧实现用 `cmap = _FONT_CMAP.get(path)` / `if cmap is None: <重算>`，
        写入的负结果永远读不回来——每次调用都白跑一遍 fontTools 导入尝试 +
        静态清单查找（与 kv_utils 记录的「故障与无记录折叠」同类缺陷）。
        """
        class _Font:
            path = os.path.join(fonts._FONT_DIR, "no_such_font.otf")

        calls = []
        monkeypatch.setattr(fonts, "_load_static_cmap", lambda p: (calls.append(p), None)[1])
        fonts._FONT_CMAP.clear()
        assert fonts._font_covers(_Font(), "羽") is False   # 内置子集无法自证：不放行
        assert fonts._FONT_CMAP.get(_Font.path) is None
        assert _Font.path in fonts._FONT_CMAP               # 负结果已缓存（键存在）
        assert fonts._font_covers(_Font(), "羽") is False
        assert len(calls) == 1                              # 第二次不再重算
