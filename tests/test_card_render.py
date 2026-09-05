"""牌面渲染与图片生命周期单测：渲染冒烟、临时图片清理（字体见 test_fonts.py）。
——牌面是牌灵的脸，画不好看怎么见人？"""
import asyncio
import os
import tempfile

import pytest

import card_render


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
            {"card": ("major", "13", "死神", "Death", "结束新生", "停滞不前"), "upright": True},
            {"card": ("cups", "2", "圣杯二", "Two of Cups", "两情相悦", "关系失衡"), "upright": False},
            {"card": ("wands", "1", "权杖一", "Ace of Wands", "灵感勃发", "阻碍重重"), "upright": True},
        ]
        path = card_render.render_cards(positions, picks, "羽时三刻", save_dir=str(tmp_path))
        assert os.path.isfile(path)
        assert seen[-1] == picks[1]["card"]  # 随机命中第 2 张时背景就是它
        assert all(c in {p["card"] for p in picks} for c in seen)  # 始终在本签牌内挑

    def test_build_card_background_sizes_and_dim(self):
        # 牌面 cover 做底 + 深藏青遮罩：尺寸吻合、整体压暗（不漏亮底）
        from PIL import Image
        bg = card_render._build_card_background(
            ("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), 120, 80)
        assert isinstance(bg, Image.Image)
        assert bg.size == (120, 80)
        px = bg.getpixel((10, 10))
        assert sum(px[:3]) < 400  # 遮罩后最亮通道也到不了未遮罩的白底亮度

    def test_build_card_background_missing_asset_raises(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            card_render._build_card_background(
                ("major", "99", "不存在", "Nope", "无", "无"), 120, 80)

    def test_daily_card_poster_produces_png(self, tmp_path):
        # 今日牌运卡海报：竖版定型尺寸、真实素材可渲染、文件名带 daily 前缀（清理识别用）
        path = card_render._render_daily_card_img(
            ("major", "19", "太阳", "The Sun", "成功喜悦", "乌云遮日"), False,
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
                ("major", "99", "不存在", "Nope", "无", "无"), True,
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
