"""AI 总预算（issue #21 ④）：单次占卜的 AI 阶段必须有等待上界。

为什么单开一个域：这条约束横跨三层——interpret 的预算对象与两段共用截止点、
daily 的透传、main 的「一次占卜开一笔」。塞进 test_core 会被解读器用例淹掉。

判据分两类，缺一不可：
- 假时钟推进（确定性）：预算 → 本次超时上限的算法、逐次收紧、耗尽即停、
  日志只报一次；并把 _chat_once 换掉，直接看「每段拿到的到底是几秒」。
- 一次真实墙钟（约 1.2 秒）：供应商真装死 5 秒，证明预算确实接到了
  asyncio.wait_for 上。「算法全对但没接线」是这类修复最典型的假绿。

边界：不测单次 ai_timeout 的老语义（那是 test_core 的候选链用例），
也不测端到端发送延迟上界——预算只约束 AI 阶段，见 main._ensure_image 的说明。
"""
import asyncio
import logging
import time
import types

from stubs import CARD, EVENT, OK_INTERP, PICK, FakeContext, FakeProvider

from daily import DailyFortune
from interpret import AiBudget, AiInterpreter
from settings import DEFAULT_AI_BUDGET, DEFAULT_SPIRIT_STAGE_BUDGET
from tarot_core import StarTarot


class Clock:
    """假时钟：推 AiBudget 的 deadline，不让测试睡墙钟。"""

    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, sec):
        self.t += sec


class _KV:
    """最小 KV 桩：够 DailyFortune 的缓存读写跑通。"""

    def __init__(self):
        self.store = {}

    async def get_kv_data(self, key, default=None):
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        self.store[key] = value


def _interp(providers, using=None, **kw):
    return AiInterpreter(FakeContext(using=using, others=providers), **kw)


def _spy_chat(monkeypatch, clock, step, results):
    """把 _chat_once 换成「记下本次超时 → 推进假时钟 → 返回台本」的桩。

    记录列表元素是 (provider.pid, timeout)：timeout 就是预算算给这次请求的上限。
    """
    seen = []
    result = results if isinstance(results, dict) else None

    async def chat(self, provider, prompt, system, timeout=None):
        seen.append((provider.pid, timeout))
        if clock is not None:
            clock.advance(step)
        return result[provider.pid] if result is not None else results

    monkeypatch.setattr(AiInterpreter, "_chat_once", chat)
    return seen


# ---------- 算法层：预算 → 本次超时上限 ----------
class TestBudgetArithmetic:
    def test_unlimited_keeps_per_call_timeout_and_stage_cap(self):
        bd = AiBudget(0)
        assert bd.limited is False
        assert bd.remaining() == float("inf")
        assert bd.step_timeout(30) == 30
        # 阶段上限是独立的第二约束：总预算关掉了，牌灵的话照样不许跑满 30 秒
        assert bd.step_timeout(30, DEFAULT_SPIRIT_STAGE_BUDGET) == float(DEFAULT_SPIRIT_STAGE_BUDGET)

    def test_broken_value_falls_back_to_default_budget(self):
        # 配置写坏了（非数字）：退回默认预算，而不是当成「不限制」把上界取消
        bd = AiBudget("oops")
        assert bd.limited is True
        assert bd.seconds == float(DEFAULT_AI_BUDGET)

    def test_remaining_shrinks_timeout_then_stops_before_min_attempt(self):
        clk = Clock()
        bd = AiBudget(120, clk)
        assert bd.step_timeout(30) == 30
        clk.advance(100)
        assert bd.step_timeout(30) == 20       # 剩余预算成为上限
        clk.advance(19.5)                      # 只剩 0.5 秒
        assert bd.step_timeout(30) is None     # 不够一次有效请求：不发，别白等一次超时
        clk.advance(1)
        assert bd.remaining() <= 0
        assert bd.step_timeout(30) is None


# ---------- 候选链：重试与回退都不重置截止点 ----------
class TestCandidateChain:
    def test_candidate_retries_share_one_deadline(self, monkeypatch):
        """四个候选、预算只够三次尝试：拿到的超时逐次收紧，用完即停。"""
        clk = Clock()
        provs = [FakeProvider(f"p{i}", result="结构失格的一段话") for i in range(4)]
        it = _interp(provs, ai_timeout=30)
        seen = _spy_chat(monkeypatch, clk, 15, "结构失格的一段话")
        out = asyncio.run(it.interpret(EVENT, "羽签", ["你的当下"], [PICK], "问感情",
                                       "详情", budget=AiBudget(40, clk)))
        assert out is None
        # 40 → 每次吃掉 15 秒 → 30 秒 / 25 秒 / 10 秒：同一笔预算在往后缩
        assert seen == [("p0", 30), ("p1", 25), ("p2", 10)]
        assert provs[3].calls == 0             # 第四个候选根本没被发起

    def test_spent_budget_reports_once_and_never_calls_ai(self, monkeypatch, caplog):
        clk = Clock()
        prov = FakeProvider("p0", result=OK_INTERP)
        it = _interp([prov], ai_timeout=30)
        bd = AiBudget(120, clk)
        clk.advance(120)                       # 开局即耗尽（截止点按「现在」算）
        with caplog.at_level(logging.WARNING):
            out = asyncio.run(it.interpret(EVENT, "羽签", ["你的当下"], [PICK], "问感情",
                                           "详情", budget=bd))
        assert out is None
        assert prov.calls == 0                 # 一次请求都没发出去
        assert "AI 解读" in bd.stopped
        notes = [r.getMessage() for r in caplog.records if "总预算" in r.getMessage()]
        assert len(notes) == 1 and "已耗尽" in notes[0]
        # 同一笔预算再问一次：不重试、不叠日志（多候选各自报一遍会把日志刷满）
        assert asyncio.run(it.interpret(EVENT, "羽签", ["你的当下"], [PICK], "问感情",
                                        "详情", budget=bd)) is None
        assert len([r for r in caplog.records if "总预算" in r.getMessage()]) == 1
        assert prov.calls == 0


# ---------- 两段（牌灵的话 + 解读）共用同一笔预算 ----------
class TestStagesShareTheDeadline:
    def test_spirit_spend_is_not_reset_for_interpret(self, monkeypatch):
        """同一笔预算：牌灵的话花掉 30 秒后，解读拿到的正是剩下的 30 秒。"""
        clk = Clock()
        bd = AiBudget(60, clk)
        seen = _spy_chat(monkeypatch, clk, 30,
                         {"s": "今天适合慢下来", "r": OK_INTERP})
        line = asyncio.run(_interp([FakeProvider("s")]).spirit_line(
            EVENT, [(CARD, True)], "感情", None, budget=bd))
        assert line == "今天适合慢下来"
        assert seen == [("s", float(DEFAULT_SPIRIT_STAGE_BUDGET))]   # 先被阶段上限卡在 20 秒
        out = asyncio.run(_interp([FakeProvider("r")]).interpret(
            EVENT, "羽签", ["你的当下"], [PICK], "问感情", "详情", budget=bd))
        assert out == OK_INTERP
        assert seen[1] == ("r", 30)            # 60 - 30 = 30：解读吃上一段剩下的
        assert bd.remaining() == 0

    def test_stage_cap_applies_even_when_budget_is_unlimited(self, monkeypatch):
        clk = Clock()
        seen = _spy_chat(monkeypatch, clk, 0, "嗯")
        asyncio.run(_interp([FakeProvider("s")]).spirit_line(
            EVENT, [(CARD, True)], "感情", None, budget=AiBudget(0)))
        assert seen == [("s", float(DEFAULT_SPIRIT_STAGE_BUDGET))]


# ---------- 真实墙钟：预算确实接到了 wait_for 上 ----------
class TestRealWallClock:
    def test_hanging_provider_times_out_at_budget_not_at_ai_timeout(self):
        """供应商装死 5 秒、单次超时 30 秒、总预算 1.2 秒 → 约 1.2 秒就回退。

        非真睡不可的理由：上面把 _chat_once 换掉的用例只证明「算出来的超时是多少」，
        证明不了它真的交给了 asyncio.wait_for。代价约 1.2 秒，值得。
        """
        prov = FakeProvider("slow", result=OK_INTERP, delay=5.0)
        it = _interp([prov], ai_timeout=30)
        t0 = time.monotonic()
        out = asyncio.run(it.interpret(EVENT, "羽签", ["你的当下"], [PICK], "问感情",
                                       "详情", budget=AiBudget(1.2)))
        elapsed = time.monotonic() - t0
        assert out is None
        assert prov.calls == 1                 # 预算已耗尽：不会再有第二个候选重来一次
        assert elapsed < 3.0, f"预算 1.2s 却等了 {elapsed:.2f}s 才回退"


# ---------- 回退语义与透传接线 ----------
class TestFallbackAndWiring:
    def test_exhausted_budget_falls_back_to_local_meanings(self):
        """预算耗尽 → 真解读器返回 None → 本地牌义（逐牌）非空：不会发出空消息。"""
        clk = Clock()
        prov = FakeProvider("p0", result=OK_INTERP)
        tarot = StarTarot.__new__(StarTarot)
        tarot.interpreter = _interp([prov], ai_timeout=30)
        bd = AiBudget(120, clk)
        clk.advance(120)                       # 开局即耗尽
        out = asyncio.run(tarot._ai_interpret(EVENT, "羽签", ["你的当下"], [PICK],
                                              "问感情", budget=bd))
        assert out is None and prov.calls == 0
        local = tarot._render_text("羽签", ["你的当下"], [PICK])
        assert local.strip() and "愚者" in local and "你的当下" in local

    def test_interp_cached_forwards_the_same_budget_object(self):
        seen = {}

        async def fake_ai(event, formation, positions, picks, clean,
                          persona_eff=None, budget=None):
            seen["budget"] = budget
            return OK_INTERP

        p = DailyFortune(_KV(), types.SimpleNamespace(_ai_interpret=fake_ai))
        bd = AiBudget(120)
        out = asyncio.run(p.interp_cached(None, "u1", "羽签", ["你的当下"], [PICK],
                                          "今天感情运势", None, bd))
        assert out == OK_INTERP and seen["budget"] is bd

    def test_spirit_cached_forwards_the_same_budget_object(self):
        seen = {}

        async def spirit(event, cards, topic, persona_eff, budget=None):
            seen["budget"] = budget
            return "今天适合慢下来"

        tarot = types.SimpleNamespace(
            interpreter=types.SimpleNamespace(spirit_line=spirit))
        p = DailyFortune(_KV(), tarot)
        bd = AiBudget(120)
        out = asyncio.run(p.spirit_cached(EVENT, "u1", [PICK], "今日运势", None, bd))
        assert out == "今天适合慢下来" and seen["budget"] is bd
