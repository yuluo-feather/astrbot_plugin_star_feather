"""字体子系统单测：缺字检测、回退链与缓存污染回归（字形覆盖完整性在 test_integrity）。
——想写得好看，先过字体这一关。"""
import os

import pytest
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
        try:
            f = fonts._load_font(20, text="𠀀测试")
        except RuntimeError as e:
            # 无中文字体的机器（CI 的 ubuntu runner）：候选链全灭，只剩默认字体，
            # 而它编码不了中文——此时必须指名报错（见 _font_encodes），
            # 而不是把 latin-1 的 UnicodeEncodeError 甩到绘图途中
            assert "默认字体" in str(e)
            return
        assert f is not None
        if os.name == "nt":
            assert "StarFeather" not in (getattr(f, "path", "") or "")

    def test_unencodable_default_font_is_refused(self, monkeypatch):
        """候补链只剩「编码不了」的默认字体时，要指名报错，不能把字体交出去。

        2026-10-01 下限格实测（CI ubuntu × py3.12 × Pillow 10.0.0）：位图默认字体
        在 textlength 上抛 latin-1 UnicodeEncodeError，错是从绘图途中冒出来的，
        调用方只看到一句编解码错、不知道牌面为什么没出。这里用同行为的假默认字体
        版本无关地钉住出口：内置子集缺字 + 本机无系统中文字体 = 明确的 RuntimeError。
        """
        class _BitmapDefault:
            path = None

            @staticmethod
            def getlength(text):
                raise UnicodeEncodeError("latin-1", text, 0, 1, "ordinal not in range(256)")

        monkeypatch.setattr(fonts, "_font_candidates", lambda bold: ())
        monkeypatch.setattr(fonts.ImageFont, "load_default", _BitmapDefault)
        fonts._FONT_CACHE.clear()
        with pytest.raises(RuntimeError, match="默认字体"):
            fonts._load_font(22, text="灵感火花")

    def test_load_font_keeps_builtin_when_covered(self):
        f = fonts._load_font(20, text="权杖王后正位")
        assert "StarFeather" in (getattr(f, "path", "") or "")

    def test_text_select_does_not_pollute_cache(self):
        """回归：带生僻字文本的选择不得写入缓存——否则之后同 size 的常用字渲染
        会错误沿用系统字体、丢失内置字体。

        生僻字那一步的落点随环境变（本机有系统中文字体 → 回退成功；容器里没有 →
        链尾指名报错，见 _font_encodes），但**缓存口径在两种世界里是同一条**：
        带 text 的选择一律不入 _FONT_CACHE，选不出来也不许留痕。旧写法只写了回退
        成功那一支，于是 CI 的 ubuntu × py3.12 × Pillow 10.0.0 格上第一行就抛
        RuntimeError，整条用例红——同一前提的兄弟用例
        （test_load_font_falls_back_for_missing_glyph）那次已按两种世界写开，
        这条漏了，这次补齐。
        """
        fonts._FONT_CACHE.clear()
        try:
            rare = fonts._load_font(20, text="𠀀测试")
        except RuntimeError:
            rare = None          # 本环境无候选字体接住生僻字：选择失败（合法出口）
        # 关键断言：这一次带 text 的选择不许进缓存（两种世界共同）
        assert (20, False) not in fonts._FONT_CACHE
        if rare is not None and os.name == "nt":
            assert "StarFeather" not in (getattr(rare, "path", "") or "")  # 确实回退了
        # 下一次常用字渲染仍应命中内置字体（不因上一次的选择而改道）
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
