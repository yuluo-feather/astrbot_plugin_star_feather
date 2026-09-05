"""每日牌运签文池单测：确定性挑选 / 兜底。

说人话：牌灵的话一句都不能少，同一天问一百遍也不能换词。"""
import asyncio
import os

import daily
from dailylines import _FALLBACK_SIGNATURE, SIGIL_LINES, pick_signature
from tarot_data import TAROT_CARDS

CARD = TAROT_CARDS[0]  # 愚者


class TestPickSignature:
    def test_deterministic(self):
        assert pick_signature(CARD, True, "u1", "20260826") == pick_signature(CARD, True, "u1", "20260826")

    def test_differs_between_ud(self):
        assert pick_signature(CARD, True, "u1", "20260826") != pick_signature(CARD, False, "u1", "20260826")

    def test_returns_from_pool(self):
        line = pick_signature(CARD, True, "u1", "20260826")
        assert line in SIGIL_LINES[(CARD[0], CARD[1])]["up"]

    def test_fallback_for_unknown_card(self):
        assert pick_signature(("major", "99"), True, "u1", "20260826") == _FALLBACK_SIGNATURE


class TestDailyCardOrchestration:
    """今日牌运卡编排（DailyFortune.render_daily_card）：渲染失败回退 None（不拦主流程）、
    成功返回路径并注册 300s 清理（卡片是给人存图转发的，别 30 秒就删）。
    用真类（__new__ 免构造）调用：方法缺失立即红，防止桩比真实现先进的错位。"""

    @staticmethod
    def _fortune():
        return daily.DailyFortune.__new__(daily.DailyFortune)

    def test_render_failure_falls_back_to_none(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("render boom")

        monkeypatch.setattr(daily, "_render_daily_card_img", boom)
        picks = [{"card": TAROT_CARDS[0], "upright": True}]
        assert asyncio.run(self._fortune().render_daily_card(["今日牌运"], picks, "u1")) is None

    def test_render_success_returns_path_and_schedules_cleanup(self, monkeypatch, tmp_path):
        calls = []

        def fake_render(card, upright, signature, date_text, save_dir=None):
            import re
            # date_text 形如「8月27日·周四」（无前导零、带星期）
            assert re.fullmatch(r"\d{1,2}月\d{1,2}日·周[一二三四五六日]", date_text)
            assert signature  # 签文非空
            p = os.path.join(str(tmp_path), "daily_test.png")
            open(p, "wb").write(b"PNG")
            return p

        def fake_cleanup(img, delay=30):
            calls.append((img, delay))

        monkeypatch.setattr(daily, "_render_daily_card_img", fake_render)
        monkeypatch.setattr(daily, "_schedule_image_cleanup", fake_cleanup)
        picks = [{"card": TAROT_CARDS[0], "upright": True}]
        out = asyncio.run(self._fortune().render_daily_card(["今日牌运"], picks, "u1"))
        assert out.endswith(".png")
        assert calls and calls[0][1] == 300
