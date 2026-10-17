"""工具入口单测：`divine_tool` 的直发约定（`yield` 字符串而非 MessageEventResult
或 None）与 AI 失败解释行。

从原 `test_core` 拆出来的一支（2026-10-17）：这条路径的坑与命令入口不同源——它走
Agent Loop，yield 语义只取末值、收尾由 LLM 承担，所以单独一份守着。
"""
import asyncio
import types

from astrbot.api.message_components import Plain
from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
from stubs import PICK


# ---------- llm_tool 直发约定：yield 字符串而非 MessageEventResult/None ----------
class TestDivineToolDirect:
    """divine_tool 是 llm_tool 型 asyncgen：内容必须 event.send 直发，
    收尾只 yield 字符串（让 Agent Loop 拿到结果继续、LLM 生成收尾回复）。
    若 yield MessageEventResult/None，runner 判无返回值 → Agent DONE →
    respond 空消息跳过 → 收口钩子不触发 → 群聊防并发门闩死锁。
    """

    @staticmethod
    def _plugin(throttle_remain=0, limit_block=None):
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = types.SimpleNamespace(llm_tool_cooldown=30, llm_tool_enabled=True)

        async def fake_throttle(key, cooldown):
            return throttle_remain

        async def fake_gate(event, for_command):
            return limit_block
        p.gate = types.SimpleNamespace(session_throttle=fake_throttle, check=fake_gate)

        async def fake_pick(event, text, force_daily=False, fixed_formation=""):
            return (False, "", "羽签", ["你的当下"], [PICK])
        p._pick_reading = fake_pick

        async def fake_run(event, fmt, pos, picks, question, **kw):
            yield event.chain_result([Plain("按规矩洗牌")])
        p._run_reading = fake_run
        return p

    @staticmethod
    def _evt(text="帮我算一卦"):
        from data.plugins.astrbot_plugin_star_feather.main import MessageChain
        e = types.SimpleNamespace(message_str=text, unified_msg_origin="g1")
        e.sent = []
        async def send(chain):
            e.sent.append(chain)
        e.send = send
        e.chain_result = lambda chain: MessageChain(chain=chain)
        return e

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    def test_normal_path_yields_only_str_and_sends_direct(self):
        p = self._plugin()
        evt = self._evt()
        out = self._collect(p.divine_tool(evt))
        # 只 yield 字符串（非 MessageEventResult / None）
        assert out and all(isinstance(x, str) for x in out)
        # 内容被 event.send 直发（洗牌结果）
        assert len(evt.sent) == 1
        assert evt.sent[0].chain[0].text == "按规矩洗牌"

    def test_result_object_is_sent_as_is(self):
        """直发的是结果对象本身，不是拆包重装的 MessageChain。

        结果对象是 MessageChain 的子类，另带 use_t2i_ / result_content_type 这些
        框架侧读取的字段；拆出 chain 再套一层会静默把它们丢回默认值。
        桩给结果对象打上标记属性：直发保真才留得住，重装必丢。
        """
        from data.plugins.astrbot_plugin_star_feather.main import MessageChain
        p = self._plugin()
        evt = self._evt()
        result = MessageChain(chain=[Plain("按规矩洗牌")])
        result.use_t2i_ = True
        evt.chain_result = lambda chain: result
        out = self._collect(p.divine_tool(evt))
        assert all(isinstance(x, str) for x in out)
        assert evt.sent and evt.sent[0] is result
        assert evt.sent[0].use_t2i_ is True

    def test_tool_entry_passes_a_fail_note(self):
        """AI 全挂、解读退回内置牌义时，工具入口也要有一句解释（2026-10-14 补）。

        此前 divine_tool 调 _run_reading 不传 fail_note（默认空串），于是自然语言入口的
        降级是「安静」的：用户只看到来源换了，没有一句说明。命令入口那条一直有。
        """
        p = self._plugin()
        evt = self._evt()
        seen = {}

        async def fake_run(event, fmt, pos, picks, question, **kw):
            seen.update(kw)
            yield event.chain_result([Plain("按规矩洗牌")])
        p._run_reading = fake_run
        out = self._collect(p.divine_tool(evt))
        assert all(isinstance(x, str) for x in out)
        assert seen.get("fail_note", "").strip(), (
            "工具入口没给 fail_note：AI 挂掉时又是一句解释都没有")


    def test_throttle_hint_sent_direct_and_yield_str(self):
        p = self._plugin(throttle_remain=25)
        evt = self._evt()
        out = self._collect(p.divine_tool(evt))
        assert len(evt.sent) == 1
        assert "25" in evt.sent[0].chain[0].text
        assert all(isinstance(x, str) for x in out)

    def test_limit_block_sent_direct_and_yield_str(self):
        p = self._plugin(limit_block="今天已经问过牌灵 5 次啦~")
        evt = self._evt()
        out = self._collect(p.divine_tool(evt))
        assert len(evt.sent) == 1
        assert "5 次" in evt.sent[0].chain[0].text
        assert all(isinstance(x, str) for x in out)

    def test_disabled_switch_sends_hint_and_yields_str(self):
        p = self._plugin()
        p.tarot.llm_tool_enabled = False
        evt = self._evt()
        out = self._collect(p.divine_tool(evt))
        assert len(evt.sent) == 1
        assert "未开启" in evt.sent[0].chain[0].text
        assert all(isinstance(x, str) for x in out)

    def test_empty_text_keeps_loop_alive(self):
        p = self._plugin()
        evt = self._evt(text="")
        out = self._collect(p.divine_tool(evt))
        assert out and all(isinstance(x, str) for x in out)
        assert evt.sent == []

    def test_non_reading_branches_do_not_claim_sent(self):
        """v0.5.1 防回归：未占卜的分支（空文本/节流/每日上限/未开启）不得复述
        「占卜结果已发送」——那是成功路径的收尾池文案，这里没有结果。"""
        cases = [
            (self._plugin(), self._evt(text="")),
            (self._plugin(throttle_remain=25), self._evt()),
            (self._plugin(limit_block="今天已经问过牌灵 5 次啦~"), self._evt()),
        ]
        for p, evt in cases:
            out = self._collect(p.divine_tool(evt))
            assert all("已发送" not in s for s in out)
        p = self._plugin()
        p.tarot.llm_tool_enabled = False
        out = self._collect(p.divine_tool(self._evt()))
        assert all("已发送" not in s for s in out)

    def test_early_exits_do_not_consume_random_state(self):
        """早退分支不得消耗全局随机状态：收尾池抽取只服务于真正走完的占卜。

        旧版把 `note = random.choice(TOOL_EPILOGUE)` 放在函数最开头，四条早退路径
        （开关关闭 / 空文本 / 节流 / 每日上限）都白白抽一次、抽完就扔——抽到的文案
        随后被 return 丢掉，一次也没用上。功能上无影响（全局 PRNG 无人依赖消耗
        次序），但「不走的路径别做无用功」是免费的干净度。
        """
        import random
        cases = [
            (self._plugin(), self._evt(text="")),                                # 空文本
            (self._plugin(throttle_remain=25), self._evt()),                     # 节流
            (self._plugin(limit_block="今天已经问过牌灵 5 次啦~"), self._evt()),  # 每日上限
        ]
        for p, evt in cases:
            random.seed(20260910)
            before = random.getstate()
            self._collect(p.divine_tool(evt))
            assert random.getstate() == before, "早退分支消耗了全局随机状态"
        p = self._plugin()
        p.tarot.llm_tool_enabled = False
        random.seed(20260910)
        before = random.getstate()
        self._collect(p.divine_tool(self._evt()))
        assert random.getstate() == before, "未开启分支消耗了全局随机状态"

    def test_normal_path_does_consume_random_state(self):
        """反向对照：真正走完的占卜**应当**抽一次收尾——否则上一条红线可能是假绿。"""
        import random
        random.seed(20260910)
        before = random.getstate()
        self._collect(self._plugin().divine_tool(self._evt("帮我算一卦")))
        assert random.getstate() != before, "正常路径没有抽取收尾文案，红线失去意义"

    def test_missing_umo_falls_back_to_global_key(self):
        """E 防回归：事件缺 unified_msg_origin 属性时，工具节流键必须回退
        sf_tool_cd_global（与命令入口 gating.check 的 or "global" 口径一致），
        不得拼出 sf_tool_cd_None 把不同来源锁到同一个孤儿键上。"""
        from data.plugins.astrbot_plugin_star_feather.main import StarFeatherPlugin
        p = StarFeatherPlugin.__new__(StarFeatherPlugin)
        p.tarot = types.SimpleNamespace(llm_tool_cooldown=30, llm_tool_enabled=True)
        seen = []

        async def spy_throttle(key, cooldown):
            seen.append(key)
            return 0

        async def fake_gate(event, for_command):
            return None
        p.gate = types.SimpleNamespace(session_throttle=spy_throttle, check=fake_gate)

        async def fake_pick(event, text, force_daily=False, fixed_formation=""):
            return (False, "", "羽签", ["你的当下"], [PICK])
        p._pick_reading = fake_pick

        async def fake_run(event, fmt, pos, picks, question, **kw):
            yield event.chain_result([Plain("按规矩洗牌")])
        p._run_reading = fake_run

        # 事件故意不带 unified_msg_origin（SimpleNamespace 默认无此属性）
        evt = types.SimpleNamespace(message_str="帮我算一卦")
        evt.sent = []
        async def send(chain):
            evt.sent.append(chain)
        evt.send = send
        from data.plugins.astrbot_plugin_star_feather.main import MessageChain
        evt.chain_result = lambda chain: MessageChain(chain=chain)
        out = self._collect(p.divine_tool(evt))
        assert seen and seen[0] == "sf_tool_cd_global"
        assert all(isinstance(x, str) for x in out)
        assert len(evt.sent) == 1  # 正常走到抽牌发送
