"""工具入口单测：自然语言入口的直发约定与早退分支。

工具入口仍保持「event.send 直发 + 结尾 yield 字符串」：yield MessageEventResult
或 None 会让 Agent Loop 判无返回值，收口钩子不触发、群聊防并发门闩死锁。
"""
import asyncio
import random
import types

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from data.plugins.astrbot_plugin_star_feather.app import handlers
from data.plugins.astrbot_plugin_star_feather.core.models import Reading
from data.plugins.astrbot_plugin_star_feather.core.settings import parse
from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
from stubs import PICK


def _settings(*, llm_enabled=True, cooldown=30, daily_fixed=True):
    return parse({"tool": {"llm_tool_enabled": llm_enabled,
                           "llm_tool_cooldown": cooldown},
                  "output": {"daily_fixed": daily_fixed, "send_mode": "plain",
                             "disclaimer": ""}})


class FakeReading:
    def __init__(self):
        self.requests = []
        self.run_kwargs = []

    async def prepare(self, request, uid):
        self.requests.append(request)
        return types.SimpleNamespace(request=request, uid=uid,
                                     formation=request.spread or "羽签",
                                     positions=["你的当下"], picks=[PICK],
                                     is_daily=False)

    async def run(self, prepared, ctx, **kw):
        self.run_kwargs.append(kw)
        return Reading(prepared.request, prepared.uid, prepared.formation,
                       tuple(prepared.positions), tuple(prepared.picks),
                       "sig", "AI", None, fail_note=kw.get("fail_note", ""))


class FakePresenter:
    def __init__(self, result=None):
        self.result = result
        self.used = []

    async def present(self, reading, messenger, event):
        self.used.append(reading)
        result = self.result if self.result is not None else event.chain_result(
            [Plain("按规矩洗牌")])
        await messenger.send_result(result)


def _plugin(*, throttle_remain=0, limit_block=None, settings=None, reading=None,
            presenter=None):
    settings = settings or _settings()
    reading = reading or FakeReading()
    presenter = presenter or FakePresenter()

    async def fake_throttle(key, cooldown):
        fake_throttle.key = key
        return throttle_remain

    async def fake_gate(event, for_command):
        return limit_block
    container = types.SimpleNamespace(
        settings=settings,
        gate=types.SimpleNamespace(session_throttle=fake_throttle, check=fake_gate),
        reading=reading, presenter=presenter)
    p = types.SimpleNamespace(container=container, context=types.SimpleNamespace())
    p._reading = reading
    p._presenter = presenter
    p._throttle = fake_throttle
    return p


def _evt(text="帮我算一卦", umo="g1"):
    e = types.SimpleNamespace(message_str=text)
    if umo is not None:
        e.unified_msg_origin = umo
    e.get_sender_id = lambda: "u1"
    e.sent = []

    async def send(chain):
        e.sent.append(chain)
    e.send = send
    e.chain_result = lambda chain: MessageChain(chain=chain)
    return e


def _collect(agen):
    async def run():
        return [x async for x in agen]
    return asyncio.run(run())


def _tool(p, evt, formation=""):
    return asyncio.run(handlers.tool_entry(p, evt, formation, version="0.0.0"))


class TestToolEntry:
    def test_normal_path_returns_str_and_sends_direct(self):
        p = _plugin()
        evt = _evt()
        out = _tool(p, evt)
        assert isinstance(out, str)
        assert len(evt.sent) == 1
        assert evt.sent[0].chain[0].text == "按规矩洗牌"

    def test_divine_tool_yields_only_str(self):
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        fake = _plugin()
        p.container, p.context = fake.container, fake.context
        evt = _evt()
        out = _collect(p.divine_tool(evt))
        assert out and all(isinstance(x, str) for x in out)
        assert len(evt.sent) == 1

    def test_formation_hint_only_fills_the_no_signal_case(self):
        p = _plugin()
        _tool(p, _evt("今天天气不错"), formation="羽镜")
        assert p._reading.requests[0].spread == "羽镜"

        _tool(p, _evt("考研复习"), formation="羽签")
        assert p._reading.requests[1].spread == "羽镜"  # 事业词命中，模型输入被忽略

    def test_result_object_is_sent_as_is(self):
        result = MessageChain(chain=[Plain("按规矩洗牌")])
        result.use_t2i_ = True
        p = _plugin(presenter=FakePresenter(result))
        evt = _evt()
        _tool(p, evt)
        assert evt.sent and evt.sent[0] is result
        assert evt.sent[0].use_t2i_ is True

    def test_tool_entry_passes_a_fail_note(self):
        p = _plugin()
        _tool(p, _evt())
        assert p._reading.run_kwargs[0].get("fail_note", "").strip(), (
            "工具入口没给 fail_note：AI 挂掉时又是一句解释都没有")

    def test_throttle_hint_sent_direct_and_return_str(self):
        p = _plugin(throttle_remain=25)
        evt = _evt()
        out = _tool(p, evt)
        assert len(evt.sent) == 1
        assert "25" in evt.sent[0].chain[0].text
        assert isinstance(out, str)

    def test_limit_block_sent_direct_and_return_str(self):
        p = _plugin(limit_block="今天已经问过牌灵 5 次啦~")
        evt = _evt()
        out = _tool(p, evt)
        assert len(evt.sent) == 1
        assert "5 次" in evt.sent[0].chain[0].text
        assert isinstance(out, str)

    def test_disabled_switch_sends_hint_and_returns_str(self):
        p = _plugin(settings=_settings(llm_enabled=False))
        evt = _evt()
        out = _tool(p, evt)
        assert len(evt.sent) == 1
        assert "未开启" in evt.sent[0].chain[0].text
        assert isinstance(out, str)

    def test_empty_text_keeps_loop_alive(self):
        p = _plugin()
        evt = _evt(text="")
        out = _tool(p, evt)
        assert isinstance(out, str)
        assert evt.sent == []

    def test_non_reading_branches_do_not_claim_sent(self):
        cases = [
            (_plugin(), _evt(text="")),
            (_plugin(throttle_remain=25), _evt()),
            (_plugin(limit_block="今天已经问过牌灵 5 次啦~"), _evt()),
            (_plugin(settings=_settings(llm_enabled=False)), _evt()),
        ]
        for p, evt in cases:
            assert "已发送" not in _tool(p, evt)

    def test_early_exits_do_not_consume_random_state(self):
        """早退分支不得消耗全局随机状态：收尾池抽取只服务于真正走完的占卜。"""
        cases = [
            (_plugin(), _evt(text="")),
            (_plugin(throttle_remain=25), _evt()),
            (_plugin(limit_block="今天已经问过牌灵 5 次啦~"), _evt()),
            (_plugin(settings=_settings(llm_enabled=False)), _evt()),
        ]
        for p, evt in cases:
            random.seed(20260910)
            before = random.getstate()
            _tool(p, evt)
            assert random.getstate() == before, "早退分支消耗了全局随机状态"

    def test_normal_path_does_consume_random_state(self):
        """反向对照：真正走完的占卜**应当**抽一次收尾。"""
        random.seed(20260910)
        before = random.getstate()
        _tool(_plugin(), _evt("帮我算一卦"))
        assert random.getstate() != before

    def test_missing_umo_falls_back_to_global_key(self):
        """事件缺 unified_msg_origin 时，工具节流键回退 sf_tool_cd_global。"""
        p = _plugin()
        evt = _evt(umo=None)
        _tool(p, evt)
        assert p._throttle.key == "sf_tool_cd_global"
        assert len(evt.sent) == 1
