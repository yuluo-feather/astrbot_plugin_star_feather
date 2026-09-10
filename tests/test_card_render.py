"""牌面渲染与图片生命周期单测：渲染冒烟、临时图片清理（字体见 test_fonts.py）。
——牌面是牌灵的脸，画不好看怎么见人？"""
import asyncio
import os
import tempfile

import pytest

import card_render


def _mk(suit, num, cn, en, up, down):
    """测试用的牌字典：up/down 简化为单关键词（渲染只走 brief，够用）。"""
    return {"suit": suit, "num": num, "cn": cn, "en": en,
            "up": {"keywords": [up], "what": "", "how": ""},
            "down": {"keywords": [down], "what": "", "turn": ""}}


# ---------- 渲染冒烟 ----------
class TestRenderSmoke:
    @pytest.fixture(autouse=True)
    def _reset_font_cache(self):
        import fonts
        fonts._FONT_CACHE.clear()
        yield

    def test_render_cards_produces_png(self, tmp_path):
        positions = ["过去", "现在", "未来"]
        picks = [
            {"card": _mk("wands", "13", "权杖王后", "Queen of Wands", "自信魅力", "嫉妒占有"), "upright": True},
            {"card": _mk("cups", "2", "圣杯二", "Two of Cups", "两情相悦", "关系失衡"), "upright": False},
            {"card": _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), "upright": True},
        ]
        path = card_render.render_cards(positions, picks, "羽时三刻", save_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert path.endswith(".png") and os.path.getsize(path) > 10_000

    def test_background_cache_returns_independent_copy(self):
        """背景缓存必须返回独立副本。

        调用方要在背景上作画，缓存若把本体交出去就等于把缓存当草稿纸——
        第一张卡画上去之后，后续每次渲染都会带着它。
        """
        from PIL import ImageDraw
        card = _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日")
        card_render._background_cached.cache_clear()
        first = card_render._build_card_background(card, 680, 1200)
        clean = card_render._build_card_background(card, 680, 1200).tobytes()
        ImageDraw.Draw(first).rectangle([0, 0, 40, 40], fill=(255, 0, 0, 255))
        assert card_render._build_card_background(card, 680, 1200).tobytes() == clean

    def test_card_image_cache_is_pure(self):
        """牌面处理缓存只省重算、不改输出；正逆位是两个缓存键。"""
        card = _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日")
        path = card_render._asset_path(card)
        card_render._load_card_image.cache_clear()
        up1, w1, h1 = card_render._load_card_image(path, True)
        down, w2, h2 = card_render._load_card_image(path, False)
        up2, _, _ = card_render._load_card_image(path, True)
        info = card_render._load_card_image.cache_info()
        assert (w1, h1) == (w2, h2)          # 缩放尺寸与正逆无关
        assert up1.tobytes() == up2.tobytes()  # 命中缓存：逐像素不变
        assert info.misses == 2 and info.hits == 1  # 正/逆各占一键，第三次命中

    def test_render_cards_background_drawn_from_this_reading(self, tmp_path, monkeypatch):
        # 背景从本签抽出的牌里随机挑一张（3 张阵=3选1）：绝不挑到本签之外的牌
        seen = []

        def fake_bg(card, w, h):
            seen.append(card)
            from PIL import Image
            return Image.new("RGB", (w, h), (40, 40, 60))

        monkeypatch.setattr(card_render, "_build_card_background", fake_bg)
        monkeypatch.setattr(card_render.random, "choice", lambda seq: seq[1])
        positions = ["过去", "现在", "未来"]
        picks = [
            {"card": _mk("major", "13", "死神", "Death", "结束新生", "停滞不前"), "upright": True},
            {"card": _mk("cups", "2", "圣杯二", "Two of Cups", "两情相悦", "关系失衡"), "upright": False},
            {"card": _mk("wands", "1", "权杖一", "Ace of Wands", "灵感勃发", "阻碍重重"), "upright": True},
        ]
        path = card_render.render_cards(positions, picks, "羽时三刻", save_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert seen[-1] == picks[1]["card"]  # 随机命中第 2 张时背景就是它
        assert all(c in [p["card"] for p in picks] for c in seen)  # 始终在本签牌内挑

    def test_build_card_background_sizes_and_dim(self):
        # 牌面 cover 做底 + 深藏青遮罩：尺寸吻合、整体压暗（不漏亮底）
        from PIL import Image
        bg = card_render._build_card_background(
            _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), 120, 80)
        assert isinstance(bg, Image.Image)
        assert bg.size == (120, 80)
        px = bg.getpixel((10, 10))
        assert sum(px[:3]) < 400  # 遮罩后最亮通道也到不了未遮罩的白底亮度

    def test_build_card_background_missing_asset_raises(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            card_render._build_card_background(
                _mk("major", "99", "不存在", "Nope", "无", "无"), 120, 80)

    def test_daily_card_poster_produces_png(self, tmp_path):
        # 今日牌运卡海报：竖版定型尺寸、真实素材可渲染、文件名带 daily 前缀（清理识别用）
        path = card_render._render_daily_card_img(
            _mk("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), False,
            "别把一时心情当决定，睡一觉再说。", "8月27日·周四", save_dir=str(tmp_path))
        from PIL import Image
        assert os.path.isfile(path)
        assert path.endswith(".png")
        assert Image.open(path).size == (card_render.DAILY_CARD_W, card_render.DAILY_CARD_H)

    def test_daily_card_poster_missing_asset_raises(self, tmp_path):
        # 素材缺失：向调用方抛异常（编排层负责回退，渲染层只保证 fail loudly）
        import pytest
        with pytest.raises(FileNotFoundError):
            card_render._render_daily_card_img(
                _mk("major", "99", "不存在", "Nope", "无", "无"), True,
                "签文", "8月27日·周四", save_dir=str(tmp_path))

    def test_split_signature_lines_by_punctuation(self):
        # 签文分行：优先在标点后断（标点留行尾），单句超宽才逐字回落；不丢字不截断
        from PIL import Image, ImageDraw

        import fonts
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        font = fonts._load_font(24, text="测")
        text = "别把一时心情当决定，睡一觉再说。"
        lines = card_render._split_signature_lines(d, text, font, 480)
        assert "".join(lines) == text
        assert len(lines) == 1  # 短签文（约 18 字）：足够宽度时保持单行（旧卡观感）
        # 宽度变窄：在逗号处断、标点收尾
        lines_narrow = card_render._split_signature_lines(d, text, font, 300)
        assert "".join(lines_narrow) == text
        assert len(lines_narrow) >= 2 and lines_narrow[0].endswith("，")
        # 单句超宽：逐字回落且不丢字
        long_sig = "这是一句特别长的牌灵的话用来确认换行不丢字也不截断。"
        lines2 = card_render._split_signature_lines(d, long_sig, font, 300)
        assert "".join(lines2) == long_sig
        # 短签文（18 字内）足够宽松时一行放下（旧卡同款观感）
        lines3 = card_render._split_signature_lines(d, text, font, 1000)
        assert len(lines3) == 1


# ---------- 临时图片生命周期 ----------
class TestTempCleanup:
    def test_delayed_remove(self, tmp_path):
        f = tmp_path / "tarot_test.png"
        f.write_bytes(b"x")
        asyncio.run(card_render._delayed_remove(str(f), 0))
        assert not f.exists()

    def test_delayed_remove_missing_ok(self):
        # 文件已被处理：删除不存在的文件不抛异常
        asyncio.run(card_render._delayed_remove("C:/not/exist/tarot_x.png", 0))

    def test_schedule_cleanup_no_loop_safe(self):
        # 无事件循环环境（同步测试）：静默跳过，不抛异常
        card_render._schedule_image_cleanup("C:/not/exist/tarot_x.png")
        card_render._schedule_image_cleanup(None)

    def test_stale_cleanup_only_tarot(self, tmp_path, monkeypatch):
        save_dir = tmp_path / "star_feather"
        save_dir.mkdir()
        (save_dir / "tarot_123.png").write_bytes(b"img")
        (save_dir / "tarot_456.png").write_bytes(b"img")
        (save_dir / "keep.txt").write_bytes(b"keep")
        # 把 gettempdir 指到 tmp_path，避免碰真实临时目录
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        card_render.cleanup_stale_images()
        assert not (save_dir / "tarot_123.png").exists()
        assert not (save_dir / "tarot_456.png").exists()
        assert (save_dir / "keep.txt").exists()


class TestWrapAndSignatureLines:
    """换行与签文分行：短路等价性 + 单字超宽的终止性（此前两者都零覆盖）。"""

    @staticmethod
    def _draw():
        from PIL import Image, ImageDraw
        return ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def test_wrap_text_single_line_when_fits(self):
        d = self._draw()
        font = card_render._load_font(22)
        text = "短牌义"
        assert d.textlength(text, font=font) <= 480
        assert card_render._wrap_text(d, text, font, 480) == [text]

    def test_wrap_text_empty_returns_no_line(self):
        assert card_render._wrap_text(self._draw(), "", card_render._load_font(22), 480) == []

    def test_wrap_text_splits_when_too_wide(self):
        d = self._draw()
        long_text = "很长的牌义关键词串" * 3
        lines = card_render._wrap_text(d, long_text, card_render._load_font(22), 60)
        assert len(lines) > 1 and "".join(lines) == long_text  # 拆行不丢字

    def test_signature_line_oversized_glyph_terminates(self):
        """单字宽于画布必须有限步结束：旧实现 cut 恒 0、seg 长度不减 → 死循环，
        而本函数跑在 to_thread 里，触发即静默挂死一个线程池槽位。"""
        from PIL import Image, ImageDraw
        d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        out = card_render._split_signature_lines(d, "字字字", card_render._load_font(24), 1)
        assert "".join(out) == "字字字"


class TestDailyCardLayoutInvariants:
    """海报卡几何不变量：签文引用块必须落在卡片下方、水印上方，间距可辨。

    卡底 = DAILY_CARD_Y + CARD_H，而 CARD_H 由 PAD_X / INNER_H / INFO_H 三者决定，
    改任一都会移动卡底。签文位置若写死数值就与卡片重叠或拉出大空洞——旧版正是
    字面量 992（= 176 + 748 + 68 的产物）。这里按推导关系断言，把「签文起点跟着
    卡片高度动」这条契约钉住：后人调高信息区却没让签文跟随时，第一条断言即红。
    """

    def test_card_height_is_derived_not_literal(self):
        from card_render import CARD_H, INFO_H, INNER_H, PAD_X
        assert CARD_H == PAD_X * 2 + INNER_H + INFO_H

    def test_quote_block_sits_between_card_and_watermark(self):
        from card_render import CARD_H, DAILY_CARD_H, DAILY_CARD_QUOTE_Y, DAILY_CARD_Y
        card_bottom = DAILY_CARD_Y + CARD_H
        assert card_bottom < DAILY_CARD_QUOTE_Y, "签文块与卡片重叠"
        assert DAILY_CARD_QUOTE_Y < DAILY_CARD_H - 58, "签文块压到水印上"
        gap = DAILY_CARD_QUOTE_Y - card_bottom
        assert 30 <= gap <= 120, f"卡底到签文间距 {gap}px 超出可辨区间（过小重叠、过大空洞）"


class TestDrawCardFontReuse:
    """同一张牌里同字号的字体只取一次：量宽与逐行绘制必须共用同一字体对象。

    旧版对 size=22 取了两次（量宽一次、逐行绘制一次），而两次调用能不能拿到同一个
    字体只是**隐式前提**（_font_covers 基于不同文本各自判定），不是保证。共用一个
    局部变量之后，「量的时候放得下、画的时候溢出」在结构上就不可能发生了。
    """

    def test_same_size_loaded_once_per_card(self, monkeypatch):
        from PIL import Image

        import card_render
        from tarot_data import TAROT_CARDS

        calls = []
        orig = card_render._load_font

        def spy(size, bold=False, text=None):
            calls.append((size, bold))
            return orig(size, bold, text=text)

        canvas = Image.new("RGB", (card_render.CARD_OUT_W + 40, card_render.CARD_H + 40),
                           (250, 250, 250))
        monkeypatch.setattr(card_render, "_load_font", spy)
        card_render._draw_card(canvas, 20, 20, TAROT_CARDS[0], True)
        n22 = calls.count((22, False))
        assert n22 == 1, f"牌义字号被取了 {n22} 次（应 1 次）：{calls}"
        assert len(calls) == len(set(calls)), f"同一次绘制里有重复字号：{calls}"


class TestBackgroundCacheSizing:
    """背景缓存的 key 含牌面信息，maxsize 语义是「最近 N 个 (牌, 尺寸) 组合」。

    底牌必须跟本签的牌走（改成只按尺寸缓存会让不同签共用同一张底牌），代价是
    **跨签命中率低**——每次渲染只挑一张底牌进缓存，遇到 4 张不同的牌（同尺寸）
    才填满。这里锁两件事：容量不小于最大牌阵的候选牌数、同 key 第二次必命中
    （缓存机制本身没坏）。注意别写成「同一签连渲两次必命中」——第一次只进一张，
    第二次随机挑到别张就是 miss，那个断言不成立（我写错过一次）。
    """

    def test_maxsize_covers_largest_spread(self):
        import card_render
        assert card_render._background_cached.cache_info().maxsize >= 4, (
            "maxsize 小于最大牌阵的候选底牌数：填满缓存所需的渲染次数会超过牌阵规模")

    def test_same_key_hits_second_time(self):
        import card_render
        from tarot_data import TAROT_CARDS
        card_render._background_cached.cache_clear()
        c = TAROT_CARDS[0]
        args = (c["suit"], c["num"], c["cn"], 1148, 986)
        card_render._background_cached(*args)
        i1 = card_render._background_cached.cache_info()
        card_render._background_cached(*args)
        i2 = card_render._background_cached.cache_info()
        assert i2.hits == i1.hits + 1 and i2.misses == i1.misses, f"同 key 未命中：{i1} → {i2}"
