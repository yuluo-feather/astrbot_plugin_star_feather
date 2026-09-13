"""每日牌运签文池单测：确定性挑选 / 兜底。

说人话：牌灵的话一句都不能少，同一天问一百遍也不能换词。"""
import asyncio
import os
import time
import types

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


class TestDailyCardRenderSlot:
    """海报卡渲染必须占住核心那把渲染锁。

    它与普通牌面图同是 Pillow 全尺寸合成（高清素材 + cover 放大），正是那把信号量
    注释里「防多人同时占卜时暴涨」要拦的负载；绕开它等于把最重的一条路径漏在锁外
    （每日次数限制默认关闭，没有替代闸门）。
    """

    def test_render_holds_the_shared_render_lock(self, monkeypatch, tmp_path):
        peak = 0
        live = 0

        def slow_render(card, upright, signature, date_text, save_dir=None):
            nonlocal peak, live
            live += 1
            peak = max(peak, live)      # 无锁时两条协程都在函数体内 → 峰值 2
            time.sleep(0.05)
            live -= 1
            path = os.path.join(str(tmp_path), "daily_slot.png")
            open(path, "wb").write(b"PNG")
            return path

        monkeypatch.setattr(daily, "_render_daily_card_img", slow_render)
        monkeypatch.setattr(daily, "_schedule_image_cleanup", lambda img, delay=30: None)
        df = daily.DailyFortune.__new__(daily.DailyFortune)
        df.tarot = types.SimpleNamespace(_render_lock=asyncio.Semaphore(1))
        picks = [{"card": TAROT_CARDS[0], "upright": True}]

        async def twice():
            return await asyncio.gather(
                df.render_daily_card(["今日牌运"], picks, "u1"),
                df.render_daily_card(["今日牌运"], picks, "u2"))

        outs = asyncio.run(twice())
        assert all(o and o.endswith(".png") for o in outs)
        assert peak == 1                # 同一时刻只有一次渲染在锁内

    def test_missing_lock_still_renders(self, monkeypatch, tmp_path):
        """桩对象没有 tarot / 没有锁时退化为不限并发——不得把渲染路径崩掉。

        getattr 链写错的话这条立刻红：缺属性会被 render_daily_card 的 except 吞成
        「渲染失败，回退普通牌面图」，返回 None。
        """
        def fake_render(card, upright, signature, date_text, save_dir=None):
            path = os.path.join(str(tmp_path), "daily_nolock.png")
            open(path, "wb").write(b"PNG")
            return path

        monkeypatch.setattr(daily, "_render_daily_card_img", fake_render)
        monkeypatch.setattr(daily, "_schedule_image_cleanup", lambda img, delay=30: None)
        picks = [{"card": TAROT_CARDS[0], "upright": True}]
        df = daily.DailyFortune.__new__(daily.DailyFortune)      # 连 tarot 都没有
        out = asyncio.run(df.render_daily_card(["今日牌运"], picks, "u1"))
        assert out and out.endswith(".png")
