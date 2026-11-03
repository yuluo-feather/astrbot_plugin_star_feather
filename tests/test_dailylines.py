"""每日牌运签文池单测：确定性挑选 / 兜底。

说人话：牌灵的话一句都不能少，同一天问一百遍也不能换词。"""
import asyncio
import os
import time

from data.plugins.astrbot_plugin_star_feather.core.dailylines import (
    _FALLBACK_SIGNATURE,
    SIGIL_LINES,
    pick_signature,
)
from data.plugins.astrbot_plugin_star_feather.core.tarot_data import TAROT_CARDS

CARD = TAROT_CARDS[0]  # 愚者


class TestPickSignature:
    def test_deterministic(self):
        assert pick_signature(CARD, True, "u1", "20260826") == pick_signature(CARD, True, "u1", "20260826")

    def test_differs_between_ud(self):
        assert pick_signature(CARD, True, "u1", "20260826") != pick_signature(CARD, False, "u1", "20260826")

    def test_returns_from_pool(self):
        line = pick_signature(CARD, True, "u1", "20260826")
        assert line in SIGIL_LINES[(CARD["suit"], CARD["num"])]["up"]

    def test_int_num_still_hits_pool(self):
        """上游若把 num 改成 int，签文也不能丢：取池键前防御性 str 化。

        tarot_data 现在保证 num 是字符串，但这条约束跨模块存在——哪天有人顺手
        改成 int，当天所有签文会静默退化成兜底句。
        """
        card = {"suit": CARD["suit"], "num": int(CARD["num"])}
        assert (pick_signature(card, True, "u1", "20260826")
                == pick_signature(CARD, True, "u1", "20260826"))

    def test_fallback_for_unknown_card(self):
        assert pick_signature({"suit": "major", "num": "99"}, True, "u1", "20260826") == _FALLBACK_SIGNATURE


class TestDailyCardOrchestration:
    """今日牌运卡编排（Renderer.daily_card）：渲染失败回退 None（不拦主流程）、
    成功返回路径并登记清理。"""

    @staticmethod
    def _renderer():
        from data.plugins.astrbot_plugin_star_feather.adapters.clock import Clock
        from data.plugins.astrbot_plugin_star_feather.services.render import Renderer
        return Renderer(image_dir="", send_mode="plain"), Clock()

    def test_render_failure_falls_back_to_none(self, monkeypatch):
        from data.plugins.astrbot_plugin_star_feather.services import (
            render as render_mod,
        )

        def boom(*a, **kw):
            raise RuntimeError("render boom")

        monkeypatch.setattr(render_mod, "render_daily_card", boom)
        picks = [{"card": TAROT_CARDS[0], "upright": True}]
        r, clock = self._renderer()
        assert asyncio.run(r.daily_card(picks, "u1", clock.today(), clock.date_text())) is None

    def test_render_success_returns_path_and_schedules_cleanup(self, monkeypatch, tmp_path):
        from data.plugins.astrbot_plugin_star_feather.services import (
            render as render_mod,
        )
        calls = []

        def fake_render(card, upright, signature, date_text, save_dir=None):
            import re
            assert re.fullmatch(r"\d{1,2}月\d{1,2}日·周[一二三四五六日]", date_text)
            assert signature
            p = os.path.join(str(tmp_path), "daily_test.jpg")
            open(p, "wb").write(b"PNG")
            return p

        def fake_cleanup(img, delay=None):
            calls.append((img, delay))

        monkeypatch.setattr(render_mod, "render_daily_card", fake_render)
        monkeypatch.setattr(render_mod, "_schedule_image_cleanup", fake_cleanup)
        picks = [{"card": TAROT_CARDS[0], "upright": True}]
        r, clock = self._renderer()
        out = asyncio.run(r.daily_card(picks, "u1", clock.today(), clock.date_text()))
        assert out.endswith(".jpg")
        assert calls and calls[0][0] == out

    def test_empty_picks_returns_none(self):
        r, clock = self._renderer()
        assert asyncio.run(r.daily_card([], "u1", clock.today(), clock.date_text())) is None


class TestDailyCardRenderSlot:
    """海报卡渲染必须占住渲染器那把锁。

    它与普通牌面图同是 Pillow 全尺寸合成，绕开它等于把最重的一条路径漏在锁外。
    """

    def test_render_holds_the_shared_render_lock(self, monkeypatch, tmp_path):
        from data.plugins.astrbot_plugin_star_feather.adapters.clock import Clock
        from data.plugins.astrbot_plugin_star_feather.services import (
            render as render_mod,
        )
        from data.plugins.astrbot_plugin_star_feather.services.render import Renderer
        peak = 0
        live = 0

        def slow_render(card, upright, signature, date_text, save_dir=None):
            nonlocal peak, live
            live += 1
            peak = max(peak, live)
            time.sleep(0.05)
            live -= 1
            path = os.path.join(str(tmp_path), "daily_slot.jpg")
            open(path, "wb").write(b"PNG")
            return path

        monkeypatch.setattr(render_mod, "render_daily_card", slow_render)
        monkeypatch.setattr(render_mod, "_schedule_image_cleanup", lambda img, delay=30: None)
        r = Renderer(image_dir=str(tmp_path), send_mode="plain")
        r._lock = asyncio.Semaphore(1)
        picks = [{"card": TAROT_CARDS[0], "upright": True}]
        clock = Clock()

        async def twice():
            return await asyncio.gather(
                r.daily_card(picks, "u1", clock.today(), clock.date_text()),
                r.daily_card(picks, "u2", clock.today(), clock.date_text()))

        outs = asyncio.run(twice())
        assert all(o and o.endswith(".jpg") for o in outs)
        assert peak == 1                # 同一时刻只有一次渲染在锁内
