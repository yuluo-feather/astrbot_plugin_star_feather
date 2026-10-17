"""入口编排单测：每日牌运判定与抽牌、`_run_reading` 统一流程、命令入口、
`require_prefix` 哨兵、真实全链、收尾文案池、启动横幅、收尾句端到端与窗口。

从原 `test_core` 拆出来的一支（2026-10-17）。这一份回答「一条占卜从进门到落地怎么走」——
两个入口的 yield / send 语义差别、收尾句与结果的先后，都在这里钉死。
"""
import asyncio
import types

import pytest
from data.plugins.astrbot_plugin_star_feather.daily import (
    _daily_pick,
    _is_daily_request,
)
from data.plugins.astrbot_plugin_star_feather.dailylines import pick_signature
from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
from data.plugins.astrbot_plugin_star_feather.tarot_data import (
    TAROT_CARDS,
)
from stubs import PICK, FakeContext

T = StarTarot.__new__(StarTarot)  # 绕过 __init__（不依赖 Context），供抽牌/编排用例复用


# ---------- 今日固定牌运 ----------
class TestDailyRequest:
    def test_hit_fortune_words(self):
        assert _is_daily_request("看看今天的运势")
        assert _is_daily_request("今日运势怎么样")
        assert _is_daily_request("最近运气不错吧")
        assert _is_daily_request("来张每日牌运")
        assert _is_daily_request("daily luck?")

    def test_specific_questions_not_daily(self):
        # 「今天她理我吗」是具体问题，不是运势请求
        assert not _is_daily_request("今天她理我吗")
        assert not _is_daily_request("我和她的感情")
        assert not _is_daily_request("下周面试")
        assert not _is_daily_request("")

    def test_time_words_with_specific_topic_not_daily(self):
        # 「最近/近期」不是运势词：带具体主题的问题是具体请求，
        # 不该被吞进今日固定牌（踩过：问什么都返回同一张牌同一段解读）
        assert not _is_daily_request("最近她对我什么感觉")
        assert not _is_daily_request("我最近的学业怎么样")
        assert not _is_daily_request("近期和他复合还有可能吗")
        assert not _is_daily_request("最近和男朋友吵架了，占卜一下关系")
        assert not _is_daily_request("今天她理我吗")  # 与上同旨：时间词+主题词
        assert _is_daily_request("最近怎么样")  # 无主题泛问 → 当日牌运
        assert _is_daily_request("每日一签")


class TestDailyPick:
    def test_deterministic_same_day_user(self):
        a = _daily_pick("12345", "20260824")
        b = _daily_pick("12345", "20260824")
        assert a == b
        assert a[0] in TAROT_CARDS
        assert isinstance(a[1], bool)

    def test_different_users_differ(self):
        # 10 组对比全相等概率极低（1/156^10），不会 flaky
        base = _daily_pick("u1", "20260824")
        diffs = sum(1 for i in range(10) if _daily_pick(f"u{i}", "20260824") != base)
        assert diffs >= 9

    def test_does_not_use_global_random(self):
        # 独立 RNG：调用前后全局 random 状态不受影响（用固定种子验证下一步）
        import random as _random
        _random.seed(42)
        expect = _random.random()
        _random.seed(42)
        _daily_pick("u", "20260824")
        assert _random.random() == expect

    def test_pick_orientation_is_bool_by_seed(self):
        """今日固定牌运的正逆位由种子决定：同一（用户,日期）结果稳定，且是 bool。"""
        for uid in ("u1", "u2"):
            for day in ("20260824", "20260825"):
                card, upright = _daily_pick(uid, day)
                assert card in TAROT_CARDS and isinstance(upright, bool)

# ---------- 统一抽牌 _pick_reading ----------
class TestPickReading:
    """stub 插件实例：验证「今日固定 vs 自由随机」的分流规则。"""

    @staticmethod
    def _evt(uid="u1"):
        """桩事件：resolve_sender_uid 第一级走 evt.get_sender_id()。"""
        e = types.SimpleNamespace()
        e.get_sender_id = lambda: uid
        return e

    @staticmethod
    def _plugin(daily_fixed=True, cached=None):
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = T
        p.daily_fixed = daily_fixed
        async def fake_cached(uid):
            return cached
        p.daily = types.SimpleNamespace(pick_cached=fake_cached)
        return p

    def test_daily_hit(self):
        cached = ("羽签", ["你的当下"], [PICK])
        p = self._plugin(cached=cached)
        r = asyncio.run(p._pick_reading(self._evt(), "看看今日运势"))
        assert r[0] is True and r[1] == "u1" and r[2] == "羽签" and r[3] == ["你的当下"]

    def test_daily_disabled_falls_to_random(self):
        p = self._plugin(daily_fixed=False, cached=("羽签", ["你的当下"], [PICK]))
        r = asyncio.run(p._pick_reading(self._evt(), "今日运势"))
        assert r[0] is False

    def test_single_force_daily(self):
        p = self._plugin(cached=("羽签", ["你的当下"], [PICK]))
        r = asyncio.run(p._pick_reading(self._evt(), "", force_daily=True))
        assert r[0] is True

    def test_cache_miss_falls_back_random(self):
        p = self._plugin(cached=None)
        r = asyncio.run(p._pick_reading(self._evt(), "今日运势"))
        assert r[0] is False and r[2] == "羽时三刻"  # 运势词经兜底落羽时三刻

    def test_specific_question_random_with_fixed_formation(self):
        p = self._plugin()
        r = asyncio.run(p._pick_reading(self._evt(), "我和她的感情", fixed_formation="羽签"))
        assert r[0] is False and r[2] == "羽签"

    def test_specific_question_selects_formation(self):
        p = self._plugin()
        r = asyncio.run(p._pick_reading(self._evt(), "考研复习计划"))
        assert r[0] is False and r[2] == "羽镜"

# ---------- 收尾句窗口：测试里不睡墙钟 ----------
@pytest.fixture
def no_epilogue_delay(monkeypatch):
    """把收尾句窗口清零：走命令入口的用例不必为它白等两秒。

    窗口本身另有专测（TestEpilogueWindow）；这里只是别让每个命令入口用例睡墙钟——
    那种慢会随用例数线性增长，而且测不到任何行为。
    """
    from data.plugins.astrbot_plugin_star_feather import main as sf_main
    monkeypatch.setattr(sf_main, "_EPILOGUE_DELAY_SECONDS", 0)

# ---------- 统一流程 _run_reading ----------
class _FakeTarot:
    """只实现 _run_reading 依赖的方法；_deliver 同步收拢所有输出。"""
    send_mode = "plain"  # 匹配真实现的发图判断（text_only 不渲染）
    # 收尾句窗口只看这一处判据（真实现 = send_mode == "forward"）；桩走 plain，
    # 与 send_mode 保持一致，免得桩自身比生产更宽松
    deliverer = types.SimpleNamespace(forward_result=False)
    ai_persona = "off"  # 牌灵人设：main 的 resolve_persona 从这里取

    def new_budget(self):
        # 真实现委托 AiInterpreter（tarot_core.StarTarot.new_budget）：桩只需要「有个
        # 对象可传」，各阶段桩都不读它——但 main 的「一次占卜共用一笔」接线照旧成立
        from data.plugins.astrbot_plugin_star_feather.interpret import AiBudget
        return AiBudget(120)

    async def _maybe_render_image(self, *a):
        return None
    async def _ai_interpret(self, ev, fmt, pos, picks, clean, persona_eff=None, budget=None):
        return "AI:" + str(clean)
    async def _deliver(self, ev, interp, img, fmt, pos, picks, fail_note="", preface=""):
        seg = str(interp) + ("|fail" if fail_note else "")
        if preface:
            seg = f"[{preface}]" + seg
        yield ev.plain_result(seg)


class TestRunReading:
    @staticmethod
    def _plugin(hint=True):
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = _FakeTarot()
        p.daily_card = True  # 今日牌运卡开关（output.daily_card），默认开
        p._shuffle_hint = (lambda: "hint") if hint else (lambda: None)
        async def fake_daily(event, uid, fmt, pos, picks, clean, persona_eff=None, budget=None):
            return "DAILY:" + str(clean)
        async def fake_spirit(event, uid, picks, clean, persona_eff, budget=None):
            # 默认桩=AI 失败兜底语义（池内当日句）；AI 成功路径由专门用例覆盖
            from time import strftime
            return pick_signature(picks[0]["card"], picks[0]["upright"],
                                  uid, strftime("%Y%m%d"))
        async def fake_card(topics, picks, uid):
            return None
        p.daily = types.SimpleNamespace(interp_cached=fake_daily, render_daily_card=fake_card,
                                        spirit_cached=fake_spirit)
        return p

    @staticmethod
    def _evt():
        """桩事件：记录 send 的独立消息 + plain_result 的 yield 结果。"""
        e = types.SimpleNamespace(plain_result=lambda s: s)
        e.sent = []
        async def send(chain):
            e.sent.append(chain)
        e.send = send
        return e

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    def test_one_budget_shared_by_spirit_and_interpret(self):
        """一次占卜只开一笔 AI 总预算，两段拿到的是同一个对象。

        接线松了这一处，预算就等于没有：任何一段（或候选重试）各自新开一笔，
        截止点就被悄悄往后推一次，「总时长有上界」的承诺随之失效。
        """
        p = self._plugin(hint=False)
        seen = {}

        async def fake_spirit(event, uid, picks, clean, persona_eff, budget=None):
            seen["spirit"] = budget
            return "sig"

        async def spy_interpret(ev, fmt, pos, picks, clean, persona_eff=None, budget=None):
            seen["interpret"] = budget
            return "AI"

        p.daily.spirit_cached = fake_spirit
        p.tarot._ai_interpret = spy_interpret
        self._collect(p._run_reading(self._evt(), "羽签", ["你的当下"], [PICK], "问感情"))
        assert seen["spirit"] is not None
        assert seen["spirit"] is seen["interpret"]  # 同一截止点：阶段之间不重置

    def test_daily_card_switch_off_skips_poster(self):
        # output.daily_card=False：牌运卡海报不渲染（回退普通牌面图），解读照常走缓存
        p = self._plugin(hint=False)
        p.daily_card = False
        calls = []
        async def fake_card(topics, picks, uid):
            calls.append(1)
            return None
        p.daily = types.SimpleNamespace(interp_cached=self._plugin().daily.interp_cached,
                                        render_daily_card=fake_card,
                                        spirit_cached=self._plugin().daily.spirit_cached)
        out = self._collect(p._run_reading(self._evt(), "羽签", ["你的当下"], [PICK],
                                           "今日运势", is_daily=True, daily_uid="u1"))
        assert calls == []  # 开关关：一次都不调海报渲染
        assert out[0].endswith("DAILY:今日运势")  # 解读照常（牌灵的话+缓存解读）

    def test_persona_resolved_once_for_spirit_and_reading(self, monkeypatch):
        """回归：ai.persona=random 时，牌灵的话与解读正文必须同一人格。

        旧实现只在每日牌运路径 resolve：自由随机路径里「牌灵的话」拿 resolve 出的
        eff，解读却在 interpret 内部再 resolve 一次——两次各掷各的骰子，同一签会
        出现「牌灵傲娇、正文温柔」两副腔调（v0.6.0 承诺的是一签固定同一人格）。
        """
        from data.plugins.astrbot_plugin_star_feather import main as main_mod
        seen = {}

        def counting_resolve(persona):
            seen["calls"] = seen.get("calls", 0) + 1
            return "persona#" + str(seen["calls"])

        monkeypatch.setattr(main_mod, "resolve_persona", counting_resolve)
        p = self._plugin(hint=False)

        async def spy_spirit(event, uid, picks, clean, persona_eff, budget=None):
            seen["spirit"] = persona_eff
            return "sig"

        async def spy_interpret(ev, fmt, pos, picks, clean, persona_eff=None, budget=None):
            seen["interpret"] = persona_eff
            return "AI"

        p.daily.spirit_cached = spy_spirit
        p.tarot._ai_interpret = spy_interpret
        self._collect(p._run_reading(self._evt(), "羽签", ["你的当下"], [PICK], "问感情"))
        assert seen["calls"] == 1  # 一次占卜只掷一次骰子
        assert seen["spirit"] == seen["interpret"] == "persona#1"

    def test_random_branch_ai(self):
        # 普通占卜（非每日牌运）也带牌灵的话：AI 失败兜底池内当日句（preface 裸句）
        from time import strftime
        p = self._plugin(hint=False)
        out = self._collect(p._run_reading(self._evt(), "羽签", ["你的当下"], [PICK], " 我的问题 "))
        sig = pick_signature(PICK["card"], PICK["upright"], "", strftime("%Y%m%d"))
        assert out == [f"[{sig}]AI:我的问题"]

    def test_daily_branch_uses_cache(self):
        # 今日牌运：解读走当日缓存（DAILY:），且结果带「牌灵的话」——
        # 签文与牌同源确定性：同人同日同牌同一句（preface 机制：AI 全失败也不丢）
        from time import strftime

        from data.plugins.astrbot_plugin_star_feather.dailylines import pick_signature
        p = self._plugin(hint=False)
        out = self._collect(p._run_reading(self._evt(), "羽签", ["你的当下"], [PICK],
                                           "今日运势", is_daily=True, daily_uid="u1"))
        sig = pick_signature(PICK["card"], PICK["upright"], "u1", strftime("%Y%m%d"))
        assert out == [f"[{sig}]DAILY:今日运势"]  # 裸句直出：无「牌灵的话：」前缀与引号


    def test_daily_branch_ai_spirit_line_prepended(self):
        # 牌灵的话 AI 成功：preface 用 AI 句（人设化），而非池内静态句
        p = self._plugin(hint=False)
        async def ai_spirit(event, uid, picks, clean, persona_eff, budget=None):
            return "今天适合慢下来，先照顾好自己。"
        p.daily.spirit_cached = ai_spirit
        out = self._collect(p._run_reading(self._evt(), "羽签", ["你的当下"], [PICK],
                                           "今日运势", is_daily=True, daily_uid="u1"))
        assert out == ["[今天适合慢下来，先照顾好自己。]DAILY:今日运势"]

    def test_no_daily_uid_keeps_ai_reading_with_pool_line(self):
        # 无用户标识（每日不可用）：解读回退自由随机 + AI 现场解读；
        # 牌灵的话没有缓存可挂，直接池内当日句兜底（一句不少）
        from time import strftime
        p = self._plugin(hint=False)
        out = self._collect(p._run_reading(self._evt(), "羽签", ["你的当下"], [PICK],
                                           "今日运势", is_daily=True, daily_uid=""))
        sig = pick_signature(PICK["card"], PICK["upright"], "", strftime("%Y%m%d"))
        assert out == [f"[{sig}]AI:今日运势"]

    def test_shuffle_hint_sent_separately_for_multi(self):
        # hint 不走 yield（命令入口每个 yield 各发一次、工具入口只取末值），改用
        # event.send 独立发送，两种入口语义一致；yield 结果中不再包含 hint，
        # 但牌灵的话照常随结果（preface）
        from time import strftime
        p = self._plugin(hint=True)
        evt = self._evt()
        out = self._collect(p._run_reading(evt, "羽时三刻", ["过去", "现在", "未来"],
                                           [PICK, PICK, PICK], "问题", empty_fallback="空"))
        sig = pick_signature(PICK["card"], PICK["upright"], "", strftime("%Y%m%d"))
        assert out == [f"[{sig}]AI:问题"]
        assert len(evt.sent) == 1
        assert evt.sent[0].chain[0].text == "hint"  # 独立发送一条

    def test_shuffle_hint_send_failure_falls_back_to_preface(self):
        # event.send 异常时降级：hint 并入最终发送（保留仪式感不丢），排在牌灵的话前
        from time import strftime
        p = self._plugin(hint=True)
        evt = self._evt()
        async def broken_send(chain):
            raise RuntimeError("no send")
        evt.send = broken_send
        out = self._collect(p._run_reading(evt, "羽时三刻", ["过去", "现在", "未来"],
                                           [PICK, PICK, PICK], "问题", empty_fallback="空"))
        sig = pick_signature(PICK["card"], PICK["upright"], "", strftime("%Y%m%d"))
        assert out == [f"[hint\n{sig}]AI:问题"]

    def test_no_hint_on_single_draw(self):
        # 单张牌阵（羽签/每日牌运）不发洗牌提示，与旧版行为一致；牌灵的话照常
        from time import strftime
        p = self._plugin(hint=True)
        evt = self._evt()
        out = self._collect(p._run_reading(evt, "羽签", ["你的当下"], [PICK],
                                           "问题", empty_fallback="空"))
        sig = pick_signature(PICK["card"], PICK["upright"], "", strftime("%Y%m%d"))
        assert out == [f"[{sig}]AI:问题"]
        assert evt.sent == []

    def test_empty_question_fallback(self):
        from time import strftime
        p = self._plugin(hint=False)
        out = self._collect(p._run_reading(self._evt(), "羽签", ["你的当下"], [PICK], "", empty_fallback="（空问题）"))
        sig = pick_signature(PICK["card"], PICK["upright"], "", strftime("%Y%m%d"))
        assert out == [f"[{sig}]AI:（空问题）"]

    def test_epilogue_sent_separately_for_command_entry(self, no_epilogue_delay):
        # 命令入口 epilogue=True：收尾句不再传入 _deliver（yield 里无 |epi:），
        # 改为结果之后独立 event.send 一条（与洗牌提示同模式，不进合并转发）
        from time import strftime
        p = self._plugin(hint=False)
        evt = self._evt()
        out = self._collect(p._run_reading(evt, "羽签", ["你的当下"], [PICK],
                                           "问题", epilogue=True))
        sig = pick_signature(PICK["card"], PICK["upright"], "", strftime("%Y%m%d"))
        assert out == [f"[{sig}]AI:问题"]
        assert len(evt.sent) == 1 and evt.sent[0].chain[0].text.startswith("✨ ")

    def test_no_epilogue_by_default(self):
        # 工具入口默认不传：收尾由 Agent Loop 的 LLM 回复承担，_deliver 不追加、
        # 也不独立发送；牌灵的话照常随结果
        from time import strftime
        p = self._plugin(hint=False)
        evt = self._evt()
        out = self._collect(p._run_reading(evt, "羽签", ["你的当下"], [PICK], "问题"))
        sig = pick_signature(PICK["card"], PICK["upright"], "", strftime("%Y%m%d"))
        assert out == [f"[{sig}]AI:问题"]
        assert evt.sent == []

# ---------- 命令入口 _command_entry（should_call_llm 替代后置 stop_event） ----------
class TestCommandEntry:
    """命令入口不再调用 event.stop_event()：洋葱模型下它会在响应阶段清空 result
    之后才执行，set 出一个空 STOP result → RespondStage 收到空消息（Prepare 空日志）、
    收口/统计类插件可能按「空回复」入账。改为 should_call_llm(True) 抑制默认 LLM。"""

    @staticmethod
    def _plugin():
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = _FakeTarot()
        async def fake_gate(event, for_command):
            return None
        p.gate = types.SimpleNamespace(check=fake_gate)
        async def fake_pick(event, text, force_daily=False, fixed_formation=""):
            return (False, "", "羽签", ["你的当下"], [PICK])
        p._pick_reading = fake_pick
        async def fake_run(event, fmt, pos, picks, question, **kw):
            yield event.plain_result("res")
        p._run_reading = fake_run
        return p

    @staticmethod
    def _evt():
        e = types.SimpleNamespace(message_str="/占卜 问题")
        e.calls = []
        e.sent = []
        e.should_call_llm = lambda v: e.calls.append(("should_call_llm", v))
        e.stop_event = lambda: e.calls.append(("stop_event",))
        e.plain_result = lambda s: s
        e.chain_result = lambda c: c
        async def send(chain):
            e.sent.append(chain)
        e.send = send
        return e

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    def test_normal_path_suppresses_llm_not_stop(self):
        p = self._plugin()
        evt = self._evt()
        out = self._collect(p._command_entry(evt, " 问题 ",
                                             err_tpl="占卜断了，换个时候再来。", helpable=True))
        assert out == ["res"]
        assert ("should_call_llm", True) in evt.calls
        assert ("stop_event",) not in evt.calls

    def test_help_branch_suppresses_and_no_stop(self):
        p = self._plugin()
        evt = self._evt()
        out = self._collect(p._command_entry(evt, "帮助",
                                             err_tpl="占卜断了，换个时候再来。", helpable=True))
        assert out == [p._help()], "帮助页只能产出一条，别在它后面再补一次抽牌"
        assert ("should_call_llm", True) in evt.calls
        assert ("stop_event",) not in evt.calls

    def test_help_variants_still_trigger(self):
        # 帮助请求的合规变体照常触发（整句匹配，不是子串）
        p = self._plugin()
        for q in ("help", "使用帮助", "帮我看看帮助说明", "help一下"):
            evt = self._evt()
            out = self._collect(p._command_entry(evt, q,
                                                 err_tpl="占卜断了，换个时候再来。", helpable=True))
            assert out and "星羽塔罗" in out[0], q

    def test_help_word_inside_question_not_help(self):
        # 「帮助」出现在问题正文里不是帮助请求（踩过：占卜问题写
        # 「帮助我做出更清醒的决定」，用户收到的却是帮助页）
        p = self._plugin()
        evt = self._evt()
        out = self._collect(p._command_entry(
            evt, "最近公司调岗，帮助我做出决定",
            err_tpl="占卜断了，换个时候再来。", helpable=True))
        assert out and out[0] == "res"  # 走正常抽牌流程而非帮助文本
        assert "星羽塔罗" not in out[0]

    def test_single_help_returns_help_page(self):
        """/单抽 帮助 与 /占卜 一样走帮助页，而不是真抽一张牌。"""
        p = self._plugin()
        evt = self._evt()
        out = self._collect(p.single(evt, "帮助"))
        assert out == [p._help()], f"帮助页应当只产出这一条，实际 {len(out)} 条"

    def test_error_path_hides_exception_details(self):
        # 安全回归：异常（路径/内部结构）只进日志，用户提示保持友好文案
        p = self._plugin()
        evt = self._evt()

        async def boom_gate(event, for_command):
            raise RuntimeError("C:\\secret\\plugin\\path\\leak")
        p.gate.check = boom_gate
        out = self._collect(p._command_entry(
            evt, " 问题 ", err_tpl="哼，这场占卜断了。牌灵今天状态不好，换个时候再来问。",
            helpable=True))
        assert out == ["哼，这场占卜断了。牌灵今天状态不好，换个时候再来问。"]
        joined = "".join(out)
        assert "C:" not in joined and "leak" not in joined and "RuntimeError" not in joined


class TestRequirePrefix:
    """触发门槛信任框架：waking_check（wake_prefix 触发词 / @ 机器人 / 私聊唤醒）
    与 CommandFilter 的精确匹配已在插件之前完成，插件不再重复校验前缀——
    重复校验会与 WebUI 配置的触发词打架（issue「只能 / 加指令」的根因）。"""

    def test_trusts_framework_private_bare_command(self):
        # 私聊裸命令「占卜 感情」（无 /、无 @）放行；旧实现会拦截并提示带 /
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        evt = types.SimpleNamespace(message_str="占卜 感情", is_private_chat=lambda: True)
        assert p._require_prefix(evt) is True

# ---------- 真实全链：命令入口 epilogue → 独立直发 ----------
class TestRunReadingRealChain:
    """命令入口 epilogue=True → 真实 _run_reading → 真实 TarotCore._deliver →
    真实 Deliverer（forward）：收尾句不得出现在合并转发 nodes 末尾，
    而是结果之后独立 event.send 一条（与洗牌提示同模式）。"""

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    @staticmethod
    def _evt_with_send():
        evt = types.SimpleNamespace()
        evt.result = None
        evt.get_self_id = lambda: "12345"
        def chain_result(chain):
            evt.result = chain
            return evt.result
        evt.chain_result = chain_result
        evt.sent_chain = None
        async def send(chain):
            evt.sent_chain = chain
        evt.send = send
        return evt

    def test_command_epilogue_sent_separately_not_in_nodes(self, no_epilogue_delay):
        from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
        from data.plugins.astrbot_plugin_star_feather.prompts import RESULT_EPILOGUE
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
        t = StarTarot(FakeContext(None), None)
        assert t.send_mode == "forward"  # 默认合并转发，走 forward 分支
        async def fake_interp(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            return "【第1张·过去】一段过去。\
【第2张·现在】一段现在。\
【第3张·未来】一段未来。\
【总结】结论。"
        async def fake_render(formation, positions, picks, *a):
            return None
        t._ai_interpret = fake_interp
        t._maybe_render_image = fake_render
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = t
        p._shuffle_hint = lambda: None
        async def fake_spirit(event, uid, picks, clean, persona_eff, budget=None):
            from time import strftime
            return pick_signature(picks[0]["card"], picks[0]["upright"],
                                  uid, strftime("%Y%m%d"))
        p.daily = types.SimpleNamespace(spirit_cached=fake_spirit)
        evt = self._evt_with_send()
        self._collect(p._run_reading(evt, "羽时三刻", ["过去", "现在", "未来"],
                                     [PICK, PICK, PICK], "问感情", epilogue=True))
        # 合并转发的末节点不再是收尾句（免责声明除外，需按文案池精确判定）
        nodes = evt.result[0].nodes
        tail = "".join(getattr(c, "text", "") for c in nodes[-1].content)
        assert tail not in RESULT_EPILOGUE, f"收尾句不应并入转发末节点，实际: {tail!r}"
        # 收尾句改为独立直发
        assert evt.sent_chain is not None, "收尾句应以独立消息发送"
        assert evt.sent_chain.chain[0].text in RESULT_EPILOGUE

    def test_tool_path_no_epilogue(self):
        # 工具入口不传 epilogue：最终链末尾不出现 ✨ 收尾句（LLM 收尾承担），
        # 也不独立发送收尾消息
        from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
        t = StarTarot(FakeContext(None), None)
        async def fake_interp(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            return "【第1张·过去】一段过去。\
【总结】结论。"
        async def fake_render(formation, positions, picks, *a):
            return None
        t._ai_interpret = fake_interp
        t._maybe_render_image = fake_render
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = t
        p._shuffle_hint = lambda: None
        async def fake_spirit(event, uid, picks, clean, persona_eff, budget=None):
            from time import strftime
            return pick_signature(picks[0]["card"], picks[0]["upright"],
                                  uid, strftime("%Y%m%d"))
        p.daily = types.SimpleNamespace(spirit_cached=fake_spirit)
        evt = self._evt_with_send()
        self._collect(p._run_reading(evt, "羽时三刻", ["过去"], [PICK], "问感情"))
        nodes = evt.result[0].nodes
        # 末节点不应是收尾句（工具收尾由 LLM 承担）；免责声明以 ✨ 开头，需按文案池精确判定
        from data.plugins.astrbot_plugin_star_feather.prompts import RESULT_EPILOGUE
        tail = "".join(getattr(c, "text", "") for c in nodes[-1].content)
        assert tail not in RESULT_EPILOGUE, f"工具路径不应有收尾句，实际末节点: {tail!r}"
        assert evt.sent_chain is None, "工具路径不应独立发送收尾句"

# ---------- 收尾文案池（一处维护，两入口共用） ----------
class TestEpiloguePool:
    def test_tool_epilogue_derived_from_result_pool(self):
        from data.plugins.astrbot_plugin_star_feather.prompts import (
            RESULT_EPILOGUE,
            TOOL_EPILOGUE,
        )
        assert len(RESULT_EPILOGUE) == 7
        assert len(TOOL_EPILOGUE) == len(RESULT_EPILOGUE)
        for t in TOOL_EPILOGUE:
            line = t.split("：", 1)[1]
            assert line in RESULT_EPILOGUE
        for r in RESULT_EPILOGUE:
            assert r.startswith("✨ ")

# ---------- 启动横幅 ----------
class TestStartupBanner:
    """fishing 同款启动横幅：initialize() 时 logger.info 打印 ASCII + 版本行。"""

    def test_initialize_logs_banner(self, monkeypatch):
        from data.plugins.astrbot_plugin_star_feather import main as main_mod
        from data.plugins.astrbot_plugin_star_feather.main import (
            VERSION,
            StarFeatherPlugin,
        )
        records = []

        class FakeLogger:
            def info(self, msg, *a, **k):
                records.append(msg)

        # 目标必须是模块对象：字符串写法 "main.logger" 按顶层名解析，落到 AstrBot 根目录
        # 的 main.py（launcher 入口）或压根不存在，两种都静默打空
        monkeypatch.setattr(main_mod, "logger", FakeLogger())
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)

        async def run():
            await p.initialize()

        asyncio.run(run())
        # 横幅之外还有一行配置快照（[cfg] LOAD，见 main.py 顶部「配置新鲜度自证」）：
        # 这里只守横幅本身，把诊断行滤掉——否则以后每加一行诊断都得回来改这个断言。
        banner = [m for m in records if not m.startswith("[cfg]")]
        assert len(banner) == 1
        text = banner[0]
        # ASCII 关键行（standard 字体 "Star Feather"）
        assert "/ ___|| |_ __ _ _ __  |  ___|__  __ _| |_| |__   ___ _ __" in text
        assert r"|____/ \__\__,_|_|    |_|  \___|\__,_|\__|_| |_|\___|_|" in text
        # 版本行跟在艺术字之后
        assert f"星羽塔罗 v{VERSION} ·" in text
        assert text.index("|____/") < text.index(f"星羽塔罗 v{VERSION}")

class TestCommandEntryEpilogueEndToEnd:
    """命令入口必须实打实传 epilogue=True——收尾句发不发由它决定。

    _command_entry 曾经声明 epilogue 参数却在内部写死 True（死参数）；而若照它改成
    epilogue=epilogue，divine/single 都不传 → 拿默认 False → 命令入口的收尾句静默消失，
    而且现有三个 epilogue 用例全都直接调 _run_reading、抓不到（_command_entry 自己的用例
    又把 _run_reading 换成了桩）。这条走真链：_command_entry → 真 _run_reading → 真 _deliver。
    """

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    @staticmethod
    def _evt():
        evt = types.SimpleNamespace()
        evt.result = None
        evt.calls = []
        evt.sent = []
        evt.get_self_id = lambda: "12345"
        evt.should_call_llm = lambda v: evt.calls.append(("should_call_llm", v))
        def chain_result(chain):
            evt.result = chain
            return evt.result
        evt.chain_result = chain_result
        evt.plain_result = lambda s: s
        async def send(chain):
            evt.sent.append(chain)
        evt.send = send
        return evt

    def test_command_entry_sends_epilogue(self, no_epilogue_delay):
        from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
        from data.plugins.astrbot_plugin_star_feather.prompts import RESULT_EPILOGUE
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
        t = StarTarot(FakeContext(None), None)
        async def fake_interp(event, formation, positions, picks, clean, persona_eff=None, budget=None):
            return "【第1张·过去】一段过去。\
【总结】结论。"
        async def fake_render(formation, positions, picks, *a):
            return None
        t._ai_interpret = fake_interp
        t._maybe_render_image = fake_render
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = t
        p._shuffle_hint = lambda: None
        async def fake_spirit(event, uid, picks, clean, persona_eff, budget=None):
            from time import strftime
            return pick_signature(picks[0]["card"], picks[0]["upright"], uid, strftime("%Y%m%d"))
        p.daily = types.SimpleNamespace(spirit_cached=fake_spirit)
        async def fake_gate(event, for_command):
            return None
        p.gate = types.SimpleNamespace(check=fake_gate)
        async def fake_pick(event, text, force_daily=False, fixed_formation=""):
            return (False, "", "羽时三刻", ["过去"], [PICK])
        p._pick_reading = fake_pick
        evt = self._evt()
        self._collect(p._command_entry(evt, "问感情", err_tpl="断了"))
        texts = [c.chain[0].text for c in evt.sent if getattr(c, "chain", None)]
        assert texts, "命令入口应当独立直发收尾句"
        assert texts[-1] in RESULT_EPILOGUE, f"末条直发不是收尾句：{texts!r}"

# ---------- 收尾句窗口：结果先到、收尾句后到的唯一手段 ----------
class TestEpilogueWindow:
    """命令入口的收尾句必须先等一个窗口再直发。

    为什么值得一条红线：2026-10-13 实测命令入口 5/5 倒序（洗牌提示 → 收尾句 → 结果），
    工具入口 5/5 正常；进程内次序经探针核对是「结果先提交、收尾句后提交」，差别只在
    合并转发卡的落地慢于紧随其后的纯文本。窗口被删掉或清零，倒序就静默回归——这条锁
    「窗口存在、值为正、且确实排在收尾句发送之前」。
    """

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    def test_window_runs_between_result_and_epilogue(self, monkeypatch):
        from data.plugins.astrbot_plugin_star_feather import main as sf_main
        from data.plugins.astrbot_plugin_star_feather.prompts import RESULT_EPILOGUE
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
        assert sf_main._EPILOGUE_DELAY_SECONDS > 0, "窗口为零：收尾句会抢在结果前面到"
        order = []

        async def fake_sleep(seconds):
            order.append(("window", seconds))

        # 只换 main 模块里的 asyncio 引用（那边就用在这一处等待），不动全局 asyncio
        monkeypatch.setattr(sf_main, "asyncio", types.SimpleNamespace(sleep=fake_sleep))
        t = StarTarot(FakeContext(None), None)

        async def fake_interp(event, formation, positions, picks, clean,
                              persona_eff=None, budget=None):
            return "【第1张·过去】一段过去。\
【总结】结论。"

        async def fake_render(formation, positions, picks, *a):
            return None

        t._ai_interpret = fake_interp
        t._maybe_render_image = fake_render
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = t
        p._shuffle_hint = lambda: None

        async def fake_spirit(event, uid, picks, clean, persona_eff, budget=None):
            from time import strftime
            return pick_signature(picks[0]["card"], picks[0]["upright"],
                                  uid, strftime("%Y%m%d"))

        p.daily = types.SimpleNamespace(spirit_cached=fake_spirit)
        evt = types.SimpleNamespace()
        evt.result = None
        evt.get_self_id = lambda: "12345"

        def chain_result(chain):
            evt.result = chain
            order.append(("yield", None))
            return chain

        evt.chain_result = chain_result

        async def send(chain):
            order.append(("send", chain.chain[0].text))

        evt.send = send
        self._collect(p._run_reading(evt, "羽时三刻", ["过去"], [PICK], "问感情",
                                     epilogue=True))
        assert [k for k, _ in order] == ["yield", "window", "send"], f"次序不对：{order!r}"
        assert order[1][1] == sf_main._EPILOGUE_DELAY_SECONDS
        assert order[-1][1] in RESULT_EPILOGUE

    @staticmethod
    def _harness(monkeypatch, interp_text):
        """最小 _run_reading 链路：只关心「窗口有没有睡」，返回 (plugin, event, order)。"""
        from data.plugins.astrbot_plugin_star_feather import main as sf_main
        from data.plugins.astrbot_plugin_star_feather.tarot_core import StarTarot
        order = []

        async def fake_sleep(seconds):
            order.append(("window", seconds))

        monkeypatch.setattr(sf_main, "asyncio", types.SimpleNamespace(sleep=fake_sleep))
        t = StarTarot(FakeContext(None), None)

        async def fake_interp(event, formation, positions, picks, clean,
                              persona_eff=None, budget=None):
            return interp_text

        async def fake_render(formation, positions, picks, *a):
            return None

        t._ai_interpret = fake_interp
        t._maybe_render_image = fake_render
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = t
        p._shuffle_hint = lambda: None

        async def fake_spirit(event, uid, picks, clean, persona_eff, budget=None):
            from time import strftime
            return pick_signature(picks[0]["card"], picks[0]["upright"],
                                  uid, strftime("%Y%m%d"))

        p.daily = types.SimpleNamespace(spirit_cached=fake_spirit)
        evt = types.SimpleNamespace()
        evt.result = None
        evt.get_self_id = lambda: "12345"

        def chain_result(chain):
            evt.result = chain
            order.append(("yield", None))
            return chain

        evt.chain_result = chain_result

        async def send(chain):
            order.append(("send", chain.chain[0].text))

        evt.send = send
        return p, evt, order

    def test_no_window_when_delivery_is_plain(self, monkeypatch):
        """窗口只买「合并转发卡落地慢」这一件事；plain / text_only 与收尾句同走纯文本通道，
        提交次序即落地次序，再睡 2 秒就是白等。"""
        p, evt, order = self._harness(
            monkeypatch, "【第1张·过去】一段过去。\n【总结】结论。")
        p.tarot.send_mode = "plain"
        p.tarot.deliverer.forward_result = False
        self._collect(p._run_reading(evt, "羽时三刻", ["过去"], [PICK], "问感情",
                                     epilogue=True))
        assert [k for k, _ in order] == ["yield", "send"], f"plain 模式不该睡窗口：{order!r}"

    def test_no_window_when_ai_falls_back(self, monkeypatch):
        """AI 全失败回退本地牌义时，结果同样是一条普通链消息（不是 Nodes），
        转发卡那条理由不成立，别让用户多等一个窗口。"""
        p, evt, order = self._harness(monkeypatch, None)
        self._collect(p._run_reading(evt, "羽时三刻", ["过去"], [PICK], "问感情",
                                     epilogue=True))
        assert [k for k, _ in order] == ["yield", "send"], f"回退路径不该睡窗口：{order!r}"
