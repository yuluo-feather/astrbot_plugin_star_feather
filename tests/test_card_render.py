"""牌面渲染与图片生命周期单测：渲染冒烟、临时图片清理（字体见 test_fonts.py）。
——牌面是牌灵的脸，画不好看怎么见人？"""
import asyncio
import os
import tempfile

import card_render
import pytest


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
