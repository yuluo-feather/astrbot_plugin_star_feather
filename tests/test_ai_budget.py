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
import os
import time
import types

from data.plugins.astrbot_plugin_star_feather import card_render
from data.plugins.astrbot_plugin_star_feather import interpret as interpret_mod
from data.plugins.astrbot_plugin_star_feather.daily import DailyFortune
from data.plugins.astrbot_plugin_star_feather.interpret import AiBudget, AiInterpreter
from data.plugins.astrbot_plugin_star_feather.settings import (
    DEFAULT_AI_BUDGET,
    DEFAULT_SPIRIT_STAGE_BUDGET,
)
from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
from stubs import CARD, EVENT, OK_INTERP, PICK, FakeContext, FakeProvider


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
        clk = Clock()
        bd = AiBudget(0, clk)
        assert bd.limited is False
        assert bd.remaining() == float("inf")
        assert bd.step_timeout(30) == 30
        # 阶段上限是独立的第二约束：总预算关掉了，牌灵的话照样不许跑满 30 秒
        bd.open_stage("spirit", DEFAULT_SPIRIT_STAGE_BUDGET)
        assert bd.step_timeout(30, "spirit") == float(DEFAULT_SPIRIT_STAGE_BUDGET)
        # 阶段上限是**累计**的：花掉 15 秒后只剩 5 秒，不是又给满 20 秒
        # （旧写法每个候选各取一次 20 秒，两个候选就能累积等到 40 秒）
        clk.advance(15)
        assert bd.step_timeout(30, "spirit") == float(DEFAULT_SPIRIT_STAGE_BUDGET) - 15
        clk.advance(4.5)                       # 只剩 0.5 秒
        assert bd.step_timeout(30, "spirit") is None

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
            EVENT, [(CARD, True)], "感情", None, budget=AiBudget(0, clk)))
        assert seen == [("s", float(DEFAULT_SPIRIT_STAGE_BUDGET))]


# ---------- 牌灵的话的阶段上限是「累计」，不是「每次请求」 ----------
class TestSpiritStageBudgetAccumulates:
    """牌灵的话的 20 秒是**阶段**上限，不是每次请求的上限（issue #21 ④ 复核）。

    旧写法对每个 Provider 各取一次 20 秒上限，两个连续失败的候选就能累积等到 40 秒；
    「取候选」那一步还不吃这条上限。本组把四件事分别钉住：累计、阶段用尽后停手且不
    动总预算、总预算关掉后阶段上限照样生效、取候选本身也受阶段上限约束。
    全部走假时钟，不睡墙钟（唯一一格真墙钟是 0.3 秒级的「没人被绕开」验证）。
    """

    SPIRIT = interpret_mod._STAGE_SPIRIT

    @staticmethod
    def _failing(n):
        """n 个「回了话但不合规」的候选：单行超过 40 字，判不合规后继续下一个。"""
        return [FakeProvider(f"p{i}", result="超长" * 30) for i in range(n)]

    @staticmethod
    def _spirit(it, bd):
        return asyncio.run(it.spirit_line(EVENT, [(CARD, True)], "感情", None, budget=bd))

    def test_two_failing_candidates_cannot_wait_40_seconds(self, monkeypatch):
        """每次吃 15 秒、阶段上限 20 秒 → 第二次只剩 5 秒，第三次不发起。

        旧写法下两个候选各拿满 20 秒（假时钟走到 40 秒）；本用例的判据正是「第二次
        拿到的上限＝阶段上限 − 已用时间」，它一旦被改回「每次各给 20 秒」就红。
        """
        clk = Clock()
        it = _interp(self._failing(3), ai_timeout=30)
        seen = _spy_chat(monkeypatch, clk, 15, "超长" * 30)
        assert self._spirit(it, AiBudget(120, clk)) is None
        # 20 → 已用 15 → 只剩 5：同一阶段累计，不是每次各 20
        assert seen == [("p0", 20.0), ("p1", 5.0)]
        assert seen[1][1] == float(DEFAULT_SPIRIT_STAGE_BUDGET) - 15
        assert len(seen) == 2, "阶段用尽就该停手：第三个候选不许再发起"

    def test_stage_stop_keeps_the_rest_of_the_total_budget(self, monkeypatch, caplog):
        """阶段上限用尽 ≠ 总预算耗尽：账上剩下的秒数要留给正式解读。

        供应商装死 20 秒（假时钟按真实耗时推进），牌灵的话就此收手；总预算 120 秒
        应当只被花掉那 20 秒，剩下的 100 秒仍够解读发起一次 30 秒的请求。
        """
        clk = Clock()
        it = _interp(self._failing(3), ai_timeout=30)
        _spy_chat(monkeypatch, clk, 20, "超长" * 30)
        bd = AiBudget(120, clk)
        with caplog.at_level(logging.WARNING):
            assert self._spirit(it, bd) is None
        assert self.SPIRIT in bd.stopped
        assert bd.remaining() == 100.0, "阶段上限用尽后总预算只被花掉那 20 秒"
        notes = [r.getMessage() for r in caplog.records if "上限" in r.getMessage()]
        assert notes and "20s 上限" in notes[0], f"日志要说清是阶段上限，实际 {notes}"
        assert not [r for r in caplog.records if "总预算已耗尽" in r.getMessage()], \
            "把「阶段上限用尽」写成「总预算耗尽」会误导排查：账上其实还有钱"
        # 剩下的预算真的还能发一次解读：同一笔预算，30 秒单次超时照给
        seen = _spy_chat(monkeypatch, clk, 0, {"r": OK_INTERP})
        it2 = _interp([FakeProvider("r", result=OK_INTERP)], ai_timeout=30)
        assert asyncio.run(it2.interpret(EVENT, "羽签", ["你的当下"], [PICK], "问感情",
                                         "详情", budget=bd)) == OK_INTERP
        assert seen == [("r", 30.0)]

    def test_unlimited_budget_still_honours_the_stage_cap(self, monkeypatch):
        """ai_budget_seconds=0 关的是总预算保护，牌灵的话那条阶段上限不跟着关。"""
        clk = Clock()
        it = _interp(self._failing(3), ai_timeout=30)
        seen = _spy_chat(monkeypatch, clk, 15, "超长" * 30)
        assert self._spirit(it, AiBudget(0, clk)) is None
        assert seen == [("p0", 20.0), ("p1", 5.0)]
        assert len(seen) == 2

    def test_slow_candidate_source_is_capped_by_the_stage_cap(self, monkeypatch):
        """取候选本身也在阶段上限之内（总预算关掉也一样），否则阶段上限被绕开。

        真墙钟不可免的理由同 TestRealWallClock：只有墙钟能证明那段等待确实接到了
        wait_for 上。代价约 0.3 秒。
        """
        monkeypatch.setattr(interpret_mod, "DEFAULT_SPIRIT_STAGE_BUDGET", 0.3)
        it = AiInterpreter(_SlowContext(delay=5.0), ai_timeout=30, ai_budget=0)
        t0 = time.monotonic()
        out = self._spirit(it, AiBudget(0))
        elapsed = time.monotonic() - t0
        assert out is None
        assert elapsed < 3.0, f"取候选没被阶段上限收住：等了 {elapsed:.2f}s"


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


# ---------- 端到端：预算掐掉 AI 之后，这条卦照样发得出去 ----------
class TestEndToEndDeliveryUnderSpentBudget:
    """把三层串成一条真入口跑一遍：供应商装死 → 预算到点上界 → 结果仍然出得来。

    上面三段分别证明「算得对」（算法层）、「接了线」（真实墙钟）、「回退有内容」
    （本地牌义非空），但每一段都是拆开看的；只要中间少接一根线（预算没透传到某段、
    回退结果没接进 _deliver），用户拿到的就是「等满一段 + 什么都没收到」，
    而三段单元用例全绿。验收口径 1（时间有上界）的另一半正是这条「事后仍能交付」。

    判据是配好的两条，缺一不可：
    - new_budget 计数（确定性）：一次占卜只许开一笔预算，计数 > 1 即红。它抓的是
      「谁又开了一笔」——入口两段各开一笔、解读器内部每候选各开一笔，都算。不靠
      墙钟阈值，CI 上不飘。
    - 两个供应商：第一个装死把预算烧光，第二个**一次都不许被叫到**——这是「预算耗尽
      后不得再启动下一轮请求」在端到端的形状（换供应商、换段位都不行）。
    两种失灵都做过变异实测（不是推测，实测计数与红况如下）：
    - interpret 的 bd.step_timeout 换成 self.new_budget().step_timeout → 计数 3
      （入口 1 + 两个候选各 1），两条用例都红；
    - main 里两段传的 budget 换成各自 new_budget() → 计数 3 / 2（普通占卜 / 每日
      牌运，差额来自不发起请求的那一段实参照样会开预算），两条用例也都红。
    注：单看墙钟抓不住「只在两段之间重置」（两段各 1.2s ≈ 2.4s，仍在 3.0 阈值内），
    所以结算归计数、墙钟只作旁证——「档位对了不等于判据有效」的老账。
    """

    FORMATION = "羽签"
    POSITIONS = ["你的当下"]

    def test_hung_provider_still_yields_a_sendable_reading(self, tmp_path):
        """普通占卜路径：候选链烧光预算 → 结果仍发得出去。"""
        first = FakeProvider("slow-1", result=OK_INTERP, delay=5.0)   # 装死 5 秒
        second = FakeProvider("slow-2", result=OK_INTERP, delay=5.0)  # 回退目标：不许被叫
        p, evt, path, created = self._plugin(tmp_path, [first, second])

        elapsed = self._run(p, evt, "问感情")

        assert len(created) == 1, "一次占卜只许开一笔预算，候选/供应商回退都不许重开"
        assert first.calls == 1, "能试的照试一次"
        assert second.calls == 0, "预算已耗尽：换供应商也不许重新开一笔请求"
        assert elapsed < 3.0, f"预算 1.2s，端到端却用了 {elapsed:.2f}s"
        self._assert_still_delivered(evt, path)

    def test_two_stages_share_the_deadline_through_the_entry(self, tmp_path):
        """每日牌运路径：牌灵的话与解读**两段都会发起请求**，共用同一个截止点。

        判据是「这一趟只新开了一笔预算」（new_budget 计数）——确定性判据，不靠墙钟，
        而且它同时管住两种重置：入口两段各开一笔、解读器内部每候选各开一笔。
        墙钟上界只作旁证：每段各开一笔的实现里，第二段会拿到满额预算重新等满，
        elapsed 从 1.2s 量级抬到 2.4s 量级。
        """
        first = FakeProvider("slow-1", result=OK_INTERP, delay=5.0)
        second = FakeProvider("slow-2", result=OK_INTERP, delay=5.0)
        p, evt, path, created = self._plugin(tmp_path, [first, second])

        elapsed = self._run(p, evt, "今日运势", is_daily=True, daily_uid="u1")

        assert len(created) == 1, "两段 AI 共用同一笔预算：阶段之间不许重置截止点"
        assert first.calls == 1, "牌灵的话那一段先试一次，就这一次"
        assert second.calls == 0, "第一段已把预算烧光：第二段不许重开一笔、也不许换供应商"
        assert elapsed < 3.0, f"预算 1.2s，端到端却用了 {elapsed:.2f}s"
        self._assert_still_delivered(evt, path)

    def _plugin(self, tmp_path, providers):
        """装一条真入口：真缓存层 + 真总预算 + 只换渲染（不碰 AI 与预算那条链）。

        返回 (插件, 事件, 图片路径, 新开预算清单)。最后一个元素是 new_budget 计数钩子
        的产出：挂在 AiInterpreter 上，所以无论谁新开一笔预算（入口每段、解读器每
        候选、阶段兜底）都被记下——「一次占卜一笔预算」这条契约得有个能落地的判据。

        模块一律按包路径取（见 test_fault_injection 的 _live_modules）：插件内只有
        包名一个身份，用例里的 tarot 与入口用的就是同一份，补丁不会打空。
        """
        from data.plugins.astrbot_plugin_star_feather import daily as daily_mod
        from data.plugins.astrbot_plugin_star_feather import tarot_core
        from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin

        ctx = FakeContext(using=None, others=list(providers))
        t = tarot_core.StarTarot(ctx, None)
        t.send_mode = "plain"
        t.deliverer.forward_result = False
        # 单次请求超时 30 秒、总预算 1.2 秒：差值就是这两条用例要的东西——等多久由
        # 预算说了算，不由供应商的 ai_timeout 说了算
        t.interpreter = AiInterpreter(ctx, ai_timeout=30, ai_budget=1.2)

        created = []
        real_new_budget = t.interpreter.new_budget      # 挂在最里层：tarot.new_budget 也走它

        def counting_new_budget():
            bd = real_new_budget()
            created.append(bd)
            return bd
        t.interpreter.new_budget = counting_new_budget

        path = str(tmp_path / "e2e.png")

        def fake_render_image(formation, positions, picks):
            open(path, "wb").write(b"PNG")
            return path
        t._render_image = fake_render_image

        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = t
        p.daily_card = False                        # 走普通牌面图，海报卡另有用例
        p._shuffle_hint = lambda: None              # 单张牌阵本来也不出提示
        p.daily = daily_mod.DailyFortune(_KV(), t)  # 真缓存层：牌灵的话走真透传

        evt = types.SimpleNamespace()
        evt.result = None
        evt.sent = []
        evt.get_self_id = lambda: "12345"

        def chain_result(chain):
            evt.result = chain
            return chain
        evt.chain_result = chain_result

        async def send(chain):
            evt.sent.append(chain)
        evt.send = send
        return p, evt, path, created

    def _run(self, plugin, evt, question, **kw):
        """跑一遍入口，返回墙钟耗时。"""
        t0 = time.monotonic()
        asyncio.run(self._drain(plugin, evt, question, **kw))
        return time.monotonic() - t0

    def _assert_still_delivered(self, evt, path):
        """回退之后这条卦仍然发得出去：有图、有本地文案、没有 AI 残留。"""
        assert evt.result, "AI 全败不等于这条卦没结果：必须仍有可发送的消息"
        nodes = list(evt.result)
        imgs = [c for c in nodes if type(c).__name__ == "Image"]
        assert [i.file for i in imgs] == [path], "图照发，且是送出去那一刻仍存在的那张"
        assert os.path.exists(path)
        # 图片模式的回退形状（deliver.deliver：有图就不再叠逐牌牌义，那是卡面的活）：
        # 图 + 牌灵的话（AI 失败时回退池内当日句）。这里要的是「文案来自本地，
        # 不是 AI 残留」，所以按标记判定而不是按某一句文案。
        texts = "".join(getattr(c, "text", "") for c in nodes).strip()
        assert texts, "牌灵的话兜底也要照发：AI 全败不等于这条消息只剩一张图"
        assert "【第" not in texts, "预算耗尽时不该出现任何 AI 正文残留"

    @staticmethod
    async def _drain(plugin, evt, question, **kw):
        async for _ in plugin._run_reading(evt, TestEndToEndDeliveryUnderSpentBudget.FORMATION,
                                           TestEndToEndDeliveryUnderSpentBudget.POSITIONS,
                                           [PICK], question, **kw):
            pass


# ---------- 候选来源本身也在预算之内（issue #21 ④-a） ----------
class _SlowContext(FakeContext):
    """候选来源慢但活着（或慢到装死）：量「取候选的等待到底有没有上界」。

    AstrBot 侧的取候选是异步的（get_using_provider_async ×2 + get_all_providers），
    这一步此前没有任何等待上限——上游卡住时，总预算就漏掉一段不受约束的等待，
    预算再准也压不住它。
    """

    def __init__(self, using=None, others=None, delay=0.0, calls=None):
        super().__init__(using=using, others=others)
        self.delay = delay
        self.calls = calls

    async def get_using_provider_async(self, umo=None):
        if self.calls is not None:
            self.calls.append(umo)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.using


class TestCandidateFetchWithinBudget:
    def _run(self, it, budget):
        return asyncio.run(it.interpret(EVENT, "羽签", ["你的当下"], [PICK], "问感情",
                                        "详情", budget=budget))

    def test_slow_candidate_source_is_capped_by_remaining_budget(self):
        """来源装死 5 秒、总预算 1.2 秒 → 约 1.2 秒就回退，不是等满 5 秒。

        真睡不可免的理由同 TestRealWallClock：只有墙钟能证明它确实接到了 wait_for 上。
        """
        it = AiInterpreter(_SlowContext(delay=5.0), ai_timeout=30)
        t0 = time.monotonic()
        out = self._run(it, AiBudget(1.2))
        elapsed = time.monotonic() - t0
        assert out is None
        assert elapsed < 3.0, f"取候选没有上界：等了 {elapsed:.2f}s"

    def test_candidate_wait_is_accounted_as_the_same_budget_stop(self, caplog):
        """取候选超时按同一个死因记账：该阶段进 stopped，且只落一条预算日志。"""
        it = AiInterpreter(_SlowContext(delay=5.0), ai_timeout=30)
        bd = AiBudget(1.2)
        with caplog.at_level(logging.WARNING):
            assert self._run(it, bd) is None
        assert "AI 解读" in bd.stopped       # 调用方据此不再叠一条「全败」误导日志
        notes = [r.getMessage() for r in caplog.records if "总预算" in r.getMessage()]
        assert len(notes) == 1, f"预算日志应恰好一条，实际 {notes}"

    def test_exhausted_budget_never_even_asks_for_candidates(self):
        """开局即耗尽：连候选都不去取——取用本身也是要花时间的异步动作。"""
        clk = Clock()
        seen = []
        it = AiInterpreter(_SlowContext(delay=5.0, calls=seen), ai_timeout=30)
        bd = AiBudget(120, clk)
        clk.advance(120)
        assert self._run(it, bd) is None
        assert seen == []                    # 一次都没发出去

    def test_unlimited_budget_keeps_fetching_as_before(self):
        """ai_budget_seconds=0 = 不设上限：取候选照旧直取，不新加一层超时。

        判据取「ai_timeout 只有 1 秒、来源却要 1.5 秒」——若谁把取候选挂到单次
        超时上，这一格就当场变红。
        """
        prov = FakeProvider("p0", result=OK_INTERP)
        it = AiInterpreter(_SlowContext(using=prov, delay=1.5), ai_timeout=1, ai_budget=0)
        assert self._run(it, AiBudget(0)) == OK_INTERP

    def test_candidate_fetch_timeout_does_not_freeze_the_provider(self):
        """取候选超时 ≠ 模型服务故障：不许把它记进失败冷却、拖累后面的占卜。"""
        prov = FakeProvider("p0", result=OK_INTERP)
        it = AiInterpreter(_SlowContext(using=prov, delay=5.0), ai_timeout=30, ai_cooldown=60)
        assert self._run(it, AiBudget(1.2)) is None
        assert it._fail_ts_by_provider == {}


# ---------- ai_budget_seconds=0 与图片核验的组合（主人 2026-10-13 第 3 条补充） ----------
class TestUnlimitedBudgetKeepsTheImagePipeline:
    """关掉总预算只该关掉「AI 等待的上界」，不该顺手关掉渲染与发送前核验。

    判据选最容易被漏掉的那一处：渲染成功后、发送前图被外部删掉——只要有人在两段
    之间插一句 `if budget.limited:`，补渲染就会跟着被跳过，用户拿到一条空图消息。
    本用例走真入口（_run_reading），只把渲染换成可控桩，AI 与预算那条链原样保留。
    """

    def test_zero_budget_rerenders_a_vanished_image(self, tmp_path, monkeypatch):
        import daily as daily_mod
        import tarot_core
        from main import StarFeatherPlugin

        monkeypatch.setattr(card_render, "IMAGE_DIR_ROOT", str(tmp_path / "star_feather"))
        ctx = FakeContext(using=None, others=[FakeProvider("p0", result=OK_INTERP)])
        t = tarot_core.StarTarot(ctx, None)
        t.send_mode = "plain"
        t.deliverer.forward_result = False
        t.interpreter = AiInterpreter(ctx, ai_timeout=30, ai_budget=0)   # 总预算关掉

        gone = str(tmp_path / "gone.png")     # 渲染给了路径，文件却在这几毫秒里没了
        alive = str(tmp_path / "alive.png")
        calls = []

        def fake_render_image(formation, positions, picks):
            calls.append(1)
            if len(calls) == 1:
                return gone
            open(alive, "wb").write(b"PNG")
            return alive
        t._render_image = fake_render_image

        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = t
        p.daily_card = False
        p._shuffle_hint = lambda: None
        p.daily = daily_mod.DailyFortune(_KV(), t)

        evt = types.SimpleNamespace()
        evt.result = None
        evt.sent = []
        evt.get_self_id = lambda: "12345"

        def chain_result(chain):
            evt.result = chain
            return chain
        evt.chain_result = chain_result

        async def send(chain):
            evt.sent.append(chain)
        evt.send = send

        asyncio.run(self._drain(p, evt))

        assert len(calls) >= 2, "预算 0 却跳过了渲染或发送前的补渲染"
        assert not os.path.exists(gone)
        nodes = list(evt.result)
        imgs = [c for c in nodes if type(c).__name__ == "Image"]
        assert [i.file for i in imgs] == [alive], "补渲染出来的那张图必须真的发出去"
        assert os.path.exists(alive)

    @staticmethod
    async def _drain(plugin, evt):
        async for _ in plugin._run_reading(evt, "羽签", ["你的当下"], [PICK], "问感情"):
            pass
