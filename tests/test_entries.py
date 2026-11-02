"""入口编排单测：意图 → 抽牌 → 占卜服务 → 呈现。

这一份回答「一条占卜从进门到落地怎么走」：ReadingService 的阶段顺序、
命令入口的帮助/限流/异常分支，以及结果先于收尾句的确定顺序。
"""
import asyncio
import types

import pytest
from data.plugins.astrbot_plugin_star_feather.ai.prompts import RESULT_EPILOGUE
from data.plugins.astrbot_plugin_star_feather.app import handlers
from data.plugins.astrbot_plugin_star_feather.clock import Clock
from data.plugins.astrbot_plugin_star_feather.daily import _daily_pick
from data.plugins.astrbot_plugin_star_feather.intent import (
    build_request,
    is_daily_request,
)
from data.plugins.astrbot_plugin_star_feather.services.reading import (
    ReadingContext,
    ReadingService,
)
from data.plugins.astrbot_plugin_star_feather.settings import parse
from data.plugins.astrbot_plugin_star_feather.tarot_data import TAROT_CARDS
from stubs import PICK


def _settings(**over):
    cfg = {"ai": {"persona": "off", "parallel_stages": True},
           "output": {"send_mode": "plain", "shuffle_lines": True,
                      "daily_fixed": True, "daily_card": True}}
    for group, values in over.items():
        cfg.setdefault(group, {}).update(values)
    return parse(cfg)


class FakeMessenger:
    def __init__(self):
        self.texts = []
        self.results = []

    async def send_text(self, text):
        self.texts.append(text)
        return True

    async def send_result(self, result):
        self.results.append(result)
        return True


class FakeDaily:
    def __init__(self, cached=None, spirit="sig", interp=None):
        self.cached = cached
        self.spirit = spirit
        self.interp = interp

    async def pick_cached(self, uid):
        return self.cached

    async def spirit_cached(self, umo, uid, picks, topic, persona_eff, budget=None):
        return self.spirit

    async def interp_cached(self, umo, uid, formation, positions, picks, clean,
                            persona_eff=None, budget=None):
        return self.interp if self.interp is not None else f"DAILY:{clean}"


class FakeInterpreter:
    def __init__(self, text="AI", capture=None):
        self.text = text
        self.capture = capture

    def new_budget(self):
        from data.plugins.astrbot_plugin_star_feather.ai.chain import AiBudget
        return AiBudget(120)

    async def interpret(self, umo, formation, positions, picks, user_input, detail,
                        persona_eff=None, budget=None):
        if self.capture is not None:
            self.capture.update(umo=umo, user_input=user_input, detail=detail,
                                persona_eff=persona_eff, budget=budget)
        return self.text


class FakeRenderer:
    def __init__(self, image=None, daily_image=None):
        self.image = image
        self.daily_image = daily_image
        self.cards = []
        self.daily_cards = []

    async def card(self, formation, positions, picks):
        self.cards.append((formation, positions, picks))
        return self.image

    async def daily_card(self, picks, uid, clock):
        self.daily_cards.append((picks, uid))
        return self.daily_image

    async def ensure_image(self, img, formation, positions, picks):
        return img


def _service(*, daily=None, interpreter=None, renderer=None, settings=None, rng=None):
    return ReadingService(
        settings=settings or _settings(), clock=Clock(),
        daily=daily or FakeDaily(), interpreter=interpreter or FakeInterpreter(),
        renderer=renderer or FakeRenderer(), rng=rng)


# ---------- 今日固定牌运 ----------
class TestDailyRequest:
    def test_hit_fortune_words(self):
        assert is_daily_request("看看今天的运势")
        assert is_daily_request("今日运势怎么样")
        assert is_daily_request("最近运气不错吧")
        assert is_daily_request("来张每日牌运")
        assert is_daily_request("daily luck?")

    def test_specific_questions_not_daily(self):
        assert not is_daily_request("今天她理我吗")
        assert not is_daily_request("我和她的感情")
        assert not is_daily_request("下周面试")
        assert not is_daily_request("")

    def test_time_words_with_specific_topic_not_daily(self):
        assert not is_daily_request("最近她对我什么感觉")
        assert not is_daily_request("我最近的学业怎么样")
        assert not is_daily_request("近期和他复合还有可能吗")
        assert not is_daily_request("最近和男朋友吵架了，占卜一下关系")
        assert not is_daily_request("今天她理我吗")
        assert is_daily_request("最近怎么样")
        assert is_daily_request("每日一签")


class TestDailyPick:
    def test_deterministic_same_day_user(self):
        a = _daily_pick("12345", "20260824")
        b = _daily_pick("12345", "20260824")
        assert a == b
        assert a[0] in TAROT_CARDS
        assert isinstance(a[1], bool)

    def test_different_users_differ(self):
        base = _daily_pick("u1", "20260824")
        diffs = sum(1 for i in range(10) if _daily_pick(f"u{i}", "20260824") != base)
        assert diffs >= 9

    def test_does_not_use_global_random(self):
        import random as _random
        _random.seed(42)
        expect = _random.random()
        _random.seed(42)
        _daily_pick("u", "20260824")
        assert _random.random() == expect

    def test_pick_orientation_is_bool_by_seed(self):
        for uid in ("u1", "u2"):
            for day in ("20260824", "20260825"):
                card, upright = _daily_pick(uid, day)
                assert card in TAROT_CARDS and isinstance(upright, bool)


# ---------- 抽牌分流 prepare ----------
class TestPrepare:
    def _service(self, cached=None):
        daily = FakeDaily(cached=cached)
        return _service(daily=daily), daily

    def test_daily_hit(self):
        cached = ("羽签", ["你的当下"], [PICK])
        svc, _ = self._service(cached)
        req = build_request("auto", "看看今日运势")
        assert req.kind == "daily" and req.fixed_for_day
        prepared = asyncio.run(svc.prepare(req, "u1"))
        assert prepared.is_daily and prepared.formation == "羽签"
        assert prepared.positions == ["你的当下"]

    def test_daily_disabled_falls_to_random(self):
        req = build_request("auto", "今日运势", daily_enabled=False)
        assert req.kind == "spread"
        svc, _ = self._service(("羽签", ["你的当下"], [PICK]))
        prepared = asyncio.run(svc.prepare(req, "u1"))
        assert prepared.formation == "羽时三刻"

    def test_single_request_never_uses_daily_cache(self):
        req = build_request("single", "")
        assert req.kind == "single" and not req.fixed_for_day
        svc, _ = self._service(("羽签", ["你的当下"], [PICK]))
        prepared = asyncio.run(svc.prepare(req, "u1"))
        assert prepared.formation == "羽签" and not prepared.is_daily

    def test_cache_miss_falls_back_random(self):
        svc, _ = self._service(None)
        req = build_request("auto", "今日运势")
        prepared = asyncio.run(svc.prepare(req, "u1"))
        assert prepared.formation == "羽签"

    def test_specific_question_uses_selected_formation(self):
        svc, _ = self._service(None)
        req = build_request("auto", "考研复习计划")
        assert req.spread == "羽镜"
        prepared = asyncio.run(svc.prepare(req, "u1"))
        assert prepared.formation == "羽镜"


# ---------- 占卜流程 run ----------
class TestRunReading:
    def _run(self, svc, *, question="问感情", epilogue=False, request=None):
        request = request or build_request("auto", question)
        prepared = asyncio.run(svc.prepare(request, "u1"))
        messenger = FakeMessenger()
        ctx = ReadingContext("u1", "umo", messenger)
        reading = asyncio.run(svc.run(prepared, ctx, question=question,
                                      epilogue=epilogue))
        return reading, messenger

    def test_one_budget_shared_by_spirit_and_interpret(self):
        """一次占卜只开一笔 AI 总预算，两段拿到的是同一个对象。"""
        seen = {}

        class Daily(FakeDaily):
            async def spirit_cached(self, umo, uid, picks, topic, persona_eff, budget=None):
                seen["spirit"] = budget
                return "sig"

        class Interp(FakeInterpreter):
            async def interpret(self, umo, formation, positions, picks, user_input,
                                detail, persona_eff=None, budget=None):
                seen["interpret"] = budget
                return "AI"

        reading, _ = self._run(_service(daily=Daily(), interpreter=Interp()))
        assert reading.interpretation == "AI"
        assert seen["spirit"] is not None
        assert seen["spirit"] is seen["interpret"]

    def test_spirit_and_interpret_run_concurrently(self):
        """两段 AI 没有数据依赖：并发执行，总耗时取较慢的一段。"""
        spirit_started = asyncio.Event()
        interpret_started = asyncio.Event()

        class Daily(FakeDaily):
            async def spirit_cached(self, umo, uid, picks, topic, persona_eff, budget=None):
                spirit_started.set()
                await asyncio.wait_for(interpret_started.wait(), timeout=1)
                return "sig"

        class Interp(FakeInterpreter):
            async def interpret(self, umo, formation, positions, picks, user_input,
                                detail, persona_eff=None, budget=None):
                interpret_started.set()
                await asyncio.wait_for(spirit_started.wait(), timeout=1)
                return "AI"

        reading, _ = self._run(_service(daily=Daily(), interpreter=Interp()))
        assert reading.spirit == "sig" and reading.interpretation == "AI"

    def test_parallel_stages_switch_serializes(self):
        """ai.parallel_stages=false：牌灵的话跑完才开解读。"""
        order = []

        class Daily(FakeDaily):
            async def spirit_cached(self, umo, uid, picks, topic, persona_eff, budget=None):
                order.append("spirit-start")
                await asyncio.sleep(0)
                order.append("spirit-end")
                return "sig"

        class Interp(FakeInterpreter):
            async def interpret(self, umo, formation, positions, picks, user_input,
                                detail, persona_eff=None, budget=None):
                order.append("interp-start")
                return "AI"

        svc = _service(daily=Daily(), interpreter=Interp(),
                       settings=_settings(ai={"parallel_stages": False}))
        self._run(svc)
        assert order == ["spirit-start", "spirit-end", "interp-start"]

    def test_parallel_stage_exception_cancels_sibling(self):
        """一段抛异常时另一段必须被取消：gather 只抛不取消，后台会继续烧 AI 预算。"""
        spirit_started = asyncio.Event()
        cancelled = asyncio.Event()

        class Daily(FakeDaily):
            async def spirit_cached(self, umo, uid, picks, topic, persona_eff, budget=None):
                spirit_started.set()
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        class Interp(FakeInterpreter):
            async def interpret(self, umo, formation, positions, picks, user_input,
                                detail, persona_eff=None, budget=None):
                await spirit_started.wait()
                raise RuntimeError("解读炸了")

        svc = _service(daily=Daily(), interpreter=Interp())
        req = build_request("auto", "问感情")
        prepared = asyncio.run(svc.prepare(req, "u1"))
        with pytest.raises(Exception):  # TaskGroup 包成 ExceptionGroup，仍是 Exception
            asyncio.run(svc.run(prepared, ReadingContext("u1", "umo", FakeMessenger()),
                                question="问感情"))
        assert cancelled.is_set(), "兄弟任务没有被取消"

    def test_daily_card_switch_off_skips_poster(self):
        renderer = FakeRenderer()
        svc = _service(daily=FakeDaily(cached=("羽签", ["你的当下"], [PICK])),
                       renderer=renderer,
                       settings=_settings(output={"daily_card": False}))
        req = build_request("daily", "今日运势")
        prepared = asyncio.run(svc.prepare(req, "u1"))
        reading = asyncio.run(svc.run(prepared, ReadingContext("u1", "umo", FakeMessenger()),
                                      question="今日运势"))
        assert renderer.daily_cards == []
        assert reading.interpretation == "DAILY:今日运势"

    def test_persona_resolved_once_for_spirit_and_reading(self, monkeypatch):
        """random 时牌灵的话与解读正文必须同一人格：一次占卜只掷一次骰子。"""
        from data.plugins.astrbot_plugin_star_feather.services import (
            reading as reading_mod,
        )
        seen = {}

        def counting_resolve(persona, seed=None):
            seen["calls"] = seen.get("calls", 0) + 1
            return "persona#" + str(seen["calls"])

        monkeypatch.setattr(reading_mod, "resolve_persona", counting_resolve)

        class Daily(FakeDaily):
            async def spirit_cached(self, umo, uid, picks, topic, persona_eff, budget=None):
                seen["spirit"] = persona_eff
                return "sig"

        class Interp(FakeInterpreter):
            async def interpret(self, umo, formation, positions, picks, user_input,
                                detail, persona_eff=None, budget=None):
                seen["interpret"] = persona_eff
                return "AI"

        self._run(_service(daily=Daily(), interpreter=Interp()))
        assert seen["calls"] == 1
        assert seen["spirit"] == seen["interpret"] == "persona#1"

    def test_random_branch_ai(self):
        reading, messenger = self._run(
            _service(daily=FakeDaily(spirit="sig"), interpreter=FakeInterpreter("AI")))
        assert reading.spirit == "sig"
        assert reading.interpretation == "AI"

    def test_daily_branch_uses_cache(self):
        reading, _ = self._run(
            _service(daily=FakeDaily(cached=("羽签", ["你的当下"], [PICK]), spirit="sig"),
                     interpreter=FakeInterpreter("AI")),
            question="今日运势", request=build_request("daily", "今日运势"))
        assert reading.interpretation == "DAILY:今日运势"

    def test_empty_question_uses_fallback(self):
        capture = {}
        svc = _service(interpreter=FakeInterpreter("AI", capture))
        self._run(svc, question="", request=build_request("spread", ""))
        assert capture["user_input"] == "（未提具体问题，请作一般运势解读）"

    def test_question_stripped_before_ai(self):
        capture = {}
        svc = _service(interpreter=FakeInterpreter("AI", capture))
        self._run(svc, question=" 我的问题 ")
        assert capture["user_input"] == "我的问题"

    def test_epilogue_only_for_command_entry(self):
        reading, _ = self._run(_service(), epilogue=True)
        assert reading.epilogue in RESULT_EPILOGUE
        reading2, _ = self._run(_service(), epilogue=False)
        assert reading2.epilogue == ""

    def test_shuffle_hint_sent_for_multi_card_only(self):
        svc = _service()
        reading, messenger = self._run(svc, question="考研复习计划")
        assert len(messenger.texts) == 1 and "洗牌中" in messenger.texts[0]
        assert reading.spirit == "sig"

        reading2, messenger2 = self._run(_service(), question="单抽")
        assert messenger2.texts == []

    def test_hint_send_failure_merges_into_preface(self):
        class BrokenMessenger(FakeMessenger):
            async def send_text(self, text):
                raise RuntimeError("send down")

        # Messenger 端口约定由适配器吞异常；这里模拟适配器返回 False
        class FalseMessenger(FakeMessenger):
            async def send_text(self, text):
                return False

        svc = _service()
        req = build_request("auto", "考研复习计划")
        prepared = asyncio.run(svc.prepare(req, "u1"))
        reading = asyncio.run(svc.run(
            prepared, ReadingContext("u1", "umo", FalseMessenger()),
            question="考研复习计划"))
        assert "洗牌中" in reading.spirit and reading.spirit.endswith("sig")


# ---------- 命令入口 ----------
def _event(uid="u1", text=""):
    e = types.SimpleNamespace(unified_msg_origin="umo", message_str=text)
    e.get_sender_id = lambda: uid
    e.get_self_id = lambda: "12345"
    e.should_call_llm = lambda v: None
    e.sent = []

    async def send(chain):
        e.sent.append(chain)
    e.send = send
    e.chain_result = lambda chain: chain
    e.plain_result = lambda t: t
    return e


def _plugin(*, settings=None, gate=None, prepare=None, reading=None, presenter=None):
    settings = settings or _settings()
    if gate is None:
        async def gate_check(event, for_command):
            return None
        gate = types.SimpleNamespace(check=gate_check, session_throttle=None)
    if prepare is None:
        async def prepare(request, uid):
            return types.SimpleNamespace(
                request=request, uid=uid, formation="羽签",
                positions=["你的当下"], picks=[PICK], is_daily=False)
    if reading is None:
        reading = ReadingService(settings=settings, clock=Clock(), daily=FakeDaily(),
                                 interpreter=FakeInterpreter(), renderer=FakeRenderer())
    if presenter is None:
        class FakePresenter:
            def __init__(self):
                self.used = []
            async def present(self, r, messenger, event):
                self.used.append(r)
                await messenger.send_text("RESULT")
        presenter = FakePresenter()
    container = types.SimpleNamespace(settings=settings, gate=gate,
                                      reading=types.SimpleNamespace(
                                          prepare=prepare, run=reading.run),
                                      presenter=presenter)
    p = types.SimpleNamespace(container=container,
                              context=types.SimpleNamespace())
    return p


class TestCommandEntry:
    def _run(self, p, ev, text="问题", **kw):
        kw.setdefault("version", "0.0.0")
        kw.setdefault("err_tpl", "断了")
        asyncio.run(handlers.command_entry(p, ev, text, **kw))

    def test_help_request_sends_help(self):
        ev = _event()
        p = _plugin()
        self._run(p, ev, "帮助")
        assert ev.sent and "星羽塔罗" in ev.sent[-1].chain[0].text
        assert p.container.presenter.used == []

    def test_gate_block_sends_message(self):
        async def block(event, for_command):
            return "BLOCK"
        p = _plugin(gate=types.SimpleNamespace(check=block, session_throttle=None))
        ev = _event()
        self._run(p, ev)
        assert ev.sent[-1].chain[0].text == "BLOCK"

    def test_error_sends_friendly_text(self):
        async def boom(request, uid):
            raise RuntimeError("internal path /secret")
        p = _plugin(prepare=boom)
        ev = _event()
        self._run(p, ev, err_tpl="断了")
        assert ev.sent[-1].chain[0].text == "断了"

    def test_intent_kind_reaches_prepare(self):
        seen = {}

        async def prepare(request, uid):
            seen["kind"] = request.kind
            return types.SimpleNamespace(request=request, uid=uid, formation="羽签",
                                         positions=["你的当下"], picks=[PICK],
                                         is_daily=False)
        p = _plugin(prepare=prepare)
        self._run(p, _event(), "", intent_kind="single")
        assert seen["kind"] == "single"

    def test_result_sent_before_epilogue(self):
        """结果先落地，收尾句后发：顺序由 await 保证，不靠窗口。"""
        from data.plugins.astrbot_plugin_star_feather.adapters.presenter import (
            Presenter,
        )
        from data.plugins.astrbot_plugin_star_feather.deliver import Deliverer
        settings = _settings()
        presenter = Presenter(Deliverer(settings.output.segment_size,
                                        settings.output.disclaimer, False))
        p = _plugin(settings=settings, presenter=presenter)
        # 让服务返回带收尾句的 Reading
        async def run(prepared, ctx, **kw):
            from data.plugins.astrbot_plugin_star_feather.core.models import Reading
            return Reading(prepared.request, "u1", "羽签", ("你的当下",), (PICK,),
                           "sig", "AI", None, epilogue="收尾")
        p.container.reading.run = run
        ev = _event()
        self._run(p, ev)
        assert len(ev.sent) == 2
        assert not isinstance(ev.sent[0], str)   # 结果链
        assert ev.sent[1].chain[0].text == "收尾"


# ---------- 单抽命令 ----------
class TestSingleCommand:
    def test_single_command_is_a_fresh_draw(self):
        seen = {}

        async def prepare(request, uid):
            seen["kind"] = request.kind
            seen["fixed"] = request.fixed_for_day
            return types.SimpleNamespace(request=request, uid=uid, formation="羽签",
                                         positions=["你的当下"], picks=[PICK],
                                         is_daily=False)
        p = _plugin(prepare=prepare)
        self._run_cmd(p, "单抽")
        assert seen == {"kind": "single", "fixed": False}

    def _run_cmd(self, p, text):
        asyncio.run(handlers.command_entry(p, _event(), text, version="0.0.0",
                                          err_tpl="断了", intent_kind="single"))


class TestPresenterFallback:
    """结果发送失败时降级纯文字，不能只剩一句收尾。"""

    def test_failed_result_falls_back_to_text(self):
        from data.plugins.astrbot_plugin_star_feather.adapters.presenter import (
            Presenter,
        )
        from data.plugins.astrbot_plugin_star_feather.core.models import (
            Reading,
            ReadingRequest,
        )
        from data.plugins.astrbot_plugin_star_feather.deliver import Deliverer

        class Messenger:
            def __init__(self):
                self.texts = []
                self.results = []

            async def send_result(self, result):
                self.results.append(result)
                return False

            async def send_text(self, text):
                self.texts.append(text)
                return True

        evt = types.SimpleNamespace(get_self_id=lambda: "0",
                                    chain_result=lambda chain: chain)
        reading = Reading(ReadingRequest("spread", "羽签", "问题", "问题", False),
                          "u1", "羽签", ("你的当下",), (PICK,), "sig", "AI", None)
        messenger = Messenger()
        asyncio.run(Presenter(Deliverer(300, "", False)).present(
            reading, messenger, evt))
        assert messenger.results
        assert any("AI" in text for text in messenger.texts)


class TestExceptionDetail:
    def test_exception_group_is_expanded(self):
        from data.plugins.astrbot_plugin_star_feather.app import handlers

        group = ExceptionGroup("unhandled errors in a TaskGroup", [ValueError("boom")])
        detail = handlers._exc_detail(group)
        assert "ValueError: boom" in detail
