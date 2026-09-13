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


class TestFontEmptyCmap:
    """fontTools 解析成功但 cmap 为空：空集不能当「全覆盖」放行。

    同一函数里两种「无数据」原本口径不一——静态清单支 `set(chars) if chars else None`
    （空 → None → 内置字体不放行），fontTools 动态支却把空 set 入缓存，
    而 all(... over 空集) 恒真，等于给「无字形字体」开绿灯。
    """

    @staticmethod
    def _fake_ttlib(monkeypatch):
        import sys
        import types as _types

        class _NonUnicodeTable:
            @staticmethod
            def isUnicode():
                return False

        class _EmptyTTFont:
            def __init__(self, path, lazy=True):
                self._tables = [_NonUnicodeTable()]

            def __getitem__(self, key):
                return _types.SimpleNamespace(tables=self._tables)

            def close(self):
                pass

        fake = _types.ModuleType("fontTools.ttLib")
        fake.TTFont = _EmptyTTFont
        monkeypatch.setitem(sys.modules, "fontTools.ttLib", fake)

    def test_empty_cmap_is_not_treated_as_full_coverage(self, monkeypatch):
        class _Font:
            path = os.path.join(fonts._FONT_DIR, "StarFeather-Regular.otf")

        self._fake_ttlib(monkeypatch)
        monkeypatch.setattr(fonts, "_load_static_cmap", lambda p: None)  # 清单也没有
        fonts._FONT_CMAP.clear()
        assert fonts._font_covers(_Font(), "羽") is False   # 内置子集：空集不自证覆盖，不放行

    def test_empty_cmap_still_passes_system_fonts(self, monkeypatch):
        """对照：系统字体在「无覆盖数据」时依旧放行（回退到它已是最后防线）。"""
        class _SysFont:
            path = os.path.join(r"C:\Windows\Fonts", "msyh.ttc")

        self._fake_ttlib(monkeypatch)
        monkeypatch.setattr(fonts, "_load_static_cmap", lambda p: None)
        fonts._FONT_CMAP.clear()
        assert fonts._font_covers(_SysFont(), "羽") is True
