"""发送层单测：长度分段 / 标记分段 / Deliverer 分发（无收尾参数）。
——结果怎么递到你手上，这里说了算。"""
import asyncio
import types

from astrbot.api.message_components import Nodes
from data.plugins.astrbot_plugin_star_feather.adapters.astrbot_io import EventMessenger
from data.plugins.astrbot_plugin_star_feather.adapters.deliver import (
    Deliverer,
    split_sections,
    split_text,
)
from data.plugins.astrbot_plugin_star_feather.adapters.event import (
    delivery_capabilities,
)
from data.plugins.astrbot_plugin_star_feather.core.tarot_data import meaning_text
from stubs import PICK


def _msg(evt):
    return EventMessenger(evt)


def _caps(evt, forward=False):
    return delivery_capabilities(evt, forward)


class TestSplitText:
    """无标记文本按长度切分：优先在换行处断开，拼接可还原原文。"""

    def test_hard_cut_join_reversible(self):
        text = "abcdefghij"
        parts = split_text(text, 5)
        assert len(parts) == 2
        assert "".join(parts) == text

    def test_newline_preferred(self):
        text = "第一行子\n第二行子"  # 9 字符 > size 7，触发切分
        parts = split_text(text, 7)
        assert parts[0].endswith("\n")
        assert "".join(parts) == text

    def test_short_text_single_part(self):
        assert split_text("短句", 300) == ["短句"]

    def test_empty(self):
        assert split_text("", 10) == []
        assert split_text(None, 10) == []

    def test_nonpositive_size_safe(self):
        """size<=0 防御：不进入 while 死循环，整体单段返回（调用方由 settings floor 保护）。"""
        assert split_text("一行文本", 0) == ["一行文本"]
        assert split_text("一行文本", -5) == ["一行文本"]
        assert split_text("", 0) == []


class TestSplitSections:
    """按【第N张·位置】/【总结】标记切分为结构化段落（不依赖换行）。"""

    def test_marked_sections(self):
        text = "【第1张·过去】一【第2张·现在】二【总结】三"
        parts = split_sections(text, 300)
        assert len(parts) == 3
        assert parts[0].startswith("【第1张·过去】")
        assert parts[2].startswith("【总结】")

    def test_less_than_two_marks_falls_back(self):
        # 只有一个标记 → 不足以切分，走长度兜底
        text = "【总结】一句话"
        assert split_sections(text, 300) == split_text(text, 300)

    def test_empty(self):
        assert split_sections("", 300) == []


class TestDeliverEpilogue:
    """Deliverer 不再承接收尾句（收尾改由 Presenter 在结果后独立发送 / LLM 回复承担），
    本类只验证：不传收尾时结果内容不受影响、无 ✨ 收尾句混入。"""

    @staticmethod
    def _evt():
        e = types.SimpleNamespace()
        e.result = None
        def chain_result(chain):
            e.result = chain
            return e.result
        e.chain_result = chain_result
        e.get_self_id = lambda: "12345"
        # 转发形态需要白名单平台名；取不到平台名现在按不支持回退普通链（review 复核）
        e.get_platform_name = lambda: "aiocqhttp"
        return e

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    def test_no_epilogue_unchanged(self):
        evt = self._evt()
        d = Deliverer(300, "")
        self._collect(d.deliver(_msg(evt), _caps(evt), "【总结】二", None,
                                "羽时三刻", ["过去"], [PICK], lambda *a: "兜底"))
        assert all(getattr(c, "text", "") != "✨ 收尾句" for c in evt.result)


    def test_message_order_image_then_preface_then_sections(self):
        """呈现顺序契约：牌面 → 牌灵的话(preface 裸句无前缀无引号) → 解读段 → 总结；免责单独一条。"""
        evt = self._evt()
        d = Deliverer(300, "免责声明")
        out = self._collect(d.deliver(_msg(evt), _caps(evt), "【第1张·过去】一【总结】二", "img.png",
                                      "羽时三刻", ["过去"], [PICK], lambda *a: "兜底",
                                      preface="今天安心"))
        assert len(out) == 2
        kinds = [type(c).__name__ for c in out[0]]
        assert kinds == ["Image", "Plain", "Plain", "Plain"]
        assert out[0][1].text == "今天安心\n"  # chain 模式 preface 带尾随换行
        assert out[1][0].text == "免责声明"

    def test_message_order_image_then_preface_forward(self):
        """合并转发模式同序：牌面节点在前，牌灵的话次之，最后免责。"""
        evt = self._evt()
        d = Deliverer(300, "免责声明")
        self._collect(d.deliver(_msg(evt), _caps(evt, True), "【总结】二", "img.png",
                                "羽时三刻", ["过去"], [PICK], lambda *a: "兜底",
                                preface="今天安心"))
        nodes = evt.result[0].nodes
        assert "✨ 星羽塔罗 · 牌面" in nodes[0].content[1].text
        assert nodes[1].content[0].text == "今天安心"
        assert "免责声明" in nodes[-1].content[0].text


class TestNoAiFallback:
    """没有 AI 解读时也走同一套末尾规则：本地牌义之后单独发送免责声明。"""

    def test_no_ai_fallback_appends_disclaimer(self):
        evt = TestDeliverEpilogue._evt()
        d = Deliverer(300, "【免责声明】")

        async def collect():
            return [x async for x in d.deliver(
                _msg(evt), _caps(evt), None, None, "羽签", ["当下"], [PICK],
                lambda *a: "本地牌义", fail_note="失败说明")]

        out = asyncio.run(collect())
        assert len(out) == 2
        assert [getattr(c, "text", "") for c in out[0]] == ["本地牌义", "失败说明"]
        assert [getattr(c, "text", "") for c in out[1]] == ["【免责声明】"]

    def test_no_ai_fallback_plain_image_then_disclaimer(self):
        """plain 图文模式：结果一条，免责声明单独一条。"""
        evt = TestDeliverEpilogue._evt()
        d = Deliverer(300, "【免责声明】")

        async def collect():
            return [x async for x in d.deliver(
                _msg(evt), _caps(evt), None, "img.png", "羽签", ["当下"], [PICK],
                lambda *a: "本地牌义")]

        out = asyncio.run(collect())
        assert len(out) == 2
        assert type(out[0][0]).__name__ == "Image"
        assert [getattr(c, "text", "") for c in out[1]] == ["【免责声明】"]

    def test_no_ai_fallback_honors_forward(self):
        """回退也遵循投递能力：forward + 白名单平台 → 逐段节点，与正常结果同构。"""
        evt = TestDeliverEpilogue._evt()
        d = Deliverer(300, "【免责声明】")

        async def collect():
            return [x async for x in d.deliver(
                _msg(evt), _caps(evt, True), None, "img.png", "羽签", ["当下"], [PICK],
                lambda *a: "本地牌义", fail_note="失败说明")]

        out = asyncio.run(collect())
        assert len(out) == 1
        nodes_result = out[0]
        assert len(nodes_result) == 1 and isinstance(nodes_result[0], Nodes)
        nodes = nodes_result[0].nodes
        assert [type(n.content[0]).__name__ for n in nodes] == ["Image", "Plain", "Plain"]
        assert nodes[0].content[0].file == "img.png"
        assert "✨ 星羽塔罗 · 牌面" in nodes[0].content[1].text
        assert nodes[1].content[0].text == "失败说明"
        assert nodes[2].content[0].text == "【免责声明】"  # 独立节点，不再与结果同节点
        assert all(n.uin == "12345" for n in nodes)

    def test_no_ai_fallback_plain_when_platform_unsupported(self):
        """平台不在白名单时，即使 send_mode=forward 也回退普通链消息。"""
        evt = TestForwardPlatformFallback._evt("webchat")
        d = Deliverer(300, "")

        async def collect():
            return [x async for x in d.deliver(
                _msg(evt), _caps(evt, True), None, "img.png", "羽签", ["当下"], [PICK],
                lambda *a: "本地牌义", fail_note="失败说明")]

        out = asyncio.run(collect())
        assert out and not any(isinstance(c, Nodes) for c in out[0])


class TestForwardPlatformFallback:
    """平台收不下 Nodes 时要自动回退普通链消息，不能静默把结果丢掉。"""

    @staticmethod
    def _evt(platform):
        e = types.SimpleNamespace()

        def chain_result(chain):
            e.result = chain
            return chain

        e.chain_result = chain_result
        e.get_self_id = lambda: "12345"
        e.get_platform_name = lambda: platform
        return e

    @staticmethod
    def _collect(d, evt):
        async def run():
            return [x async for x in d.deliver(
                _msg(evt), _caps(evt, True), "【第1张·过去】一段。\n【总结】结论。", None,
                "羽时三刻", ["过去"], [PICK], lambda *a: "兜底", preface="牌灵的话")]
        return asyncio.run(run())

    def test_webchat_falls_back_to_plain(self):
        """WebChat 的 _send 只认 Plain/Image 等，Nodes 会被静默丢弃。"""
        evt = self._evt("webchat")
        self._collect(Deliverer(300, ""), evt)
        comps = list(evt.result)
        assert not any(isinstance(c, Nodes) for c in comps), "WebChat 不该收到 Nodes"
        text = "".join(getattr(c, "text", "") for c in comps)
        assert "【总结】结论" in text and "牌灵的话" in text

    def test_qq_keeps_forward_nodes(self):
        """QQ 等支持转发的平台维持原行为：结果仍是 Nodes。"""
        evt = self._evt("aiocqhttp")
        self._collect(Deliverer(300, ""), evt)
        assert any(isinstance(c, Nodes) for c in list(evt.result))

    def test_unverified_platform_falls_back_to_plain(self):
        """白名单之外的平台（Telegram / Discord / WeCom 等）也回退普通消息。"""
        evt = self._evt("telegram")
        self._collect(Deliverer(300, ""), evt)
        comps = list(evt.result)
        assert not any(isinstance(c, Nodes) for c in comps), "未验证平台不该收到 Nodes"
        text = "".join(getattr(c, "text", "") for c in comps)
        assert "【总结】结论" in text

    def test_unknown_platform_name_falls_back_to_plain(self):
        """拿不到平台名按不支持处理：普通链总能送达，未知平台发 Nodes 可能被丢掉（review 复核）。"""
        evt = self._evt("")
        self._collect(Deliverer(300, ""), evt)
        assert not any(isinstance(c, Nodes) for c in list(evt.result))

    def test_platform_name_exception_falls_back_to_plain(self):
        """平台名查询抛异常同样回退普通消息，不许拿「诊断缺失」赌结果能送达。"""
        evt = self._evt("aiocqhttp")

        def boom():
            raise RuntimeError("no platform info")
        evt.get_platform_name = boom
        self._collect(Deliverer(300, ""), evt)
        assert not any(isinstance(c, Nodes) for c in list(evt.result))


def _plain_text(formation, positions, picks):
    """仿 core.draw.render_text 的逐牌牌义兜底文案（回归用例用）。
    用 core.tarot_data.meaning_text 渲染（与生产路径同源，防两根线走偏）。"""
    NL = chr(10)
    lines = [f"🔮 牌阵：{formation}"]
    for i, (pos, pick) in enumerate(zip(positions, picks), 1):
        card = pick["card"]
        state = "正位" if pick["upright"] else "逆位"
        meaning = meaning_text(card, bool(pick["upright"]))
        lines.append("".join([f"🃏 第{i}张 ·【{pos}】", NL,
                              f"「{card['cn']}」{state}", NL + "   ", meaning]))
    return NL.join(lines)


class TestDeliverAIlessFallback:
    """AI 失败兜底回归（缺陷 A）：interp=None + img=None + preface 非空 + picks 非空时，
    逐牌牌义必须仍送达——preface 恒非空（spirit_cached 在 picks 非空时必返非空），
    旧代码 elif not img 分支在生产路径不可达，牌义整段被吞（本类对该分支零覆盖导致漏网）。"""

    @staticmethod
    def _evt():
        e = types.SimpleNamespace()
        e.result = None

        def chain_result(chain):
            e.result = chain
            return e.result

        e.chain_result = chain_result
        e.get_self_id = lambda: "12345"
        # 转发形态需要白名单平台名；取不到平台名现在按不支持回退普通链（review 复核）
        e.get_platform_name = lambda: "aiocqhttp"
        return e

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]

        return asyncio.run(run())

    def test_no_image_preface_nonempty_keeps_per_card_meanings(self):
        evt = self._evt()
        d = Deliverer(300, "")
        positions = ["过去", "现在", "未来"]
        self._collect(d.deliver(_msg(evt), _caps(evt), None, None, "羽镜", positions, [PICK] * 3,
                                _plain_text,
                                fail_note="📖 解读：" + chr(10) + "（AI 今天闹脾气不肯开口）",
                                preface="今天安心"))
        texts = [getattr(c, "text", "") for c in evt.result]
        assert texts[0] == "今天安心"
        joined = "".join(texts[1:])
        assert "🔮 牌阵：羽镜" in joined
        assert "第1张" in joined and "第2张" in joined and "第3张" in joined
        assert "愚者" in joined and "新的开始" in joined
        assert "AI 今天闹脾气" in texts[-1]
        assert all(t for t in texts)

    def test_with_image_no_meaning_text_appended(self):
        """有图时行为不变：图内已有牌义信息区，不重复追加逐牌牌义文字。"""
        evt = self._evt()
        d = Deliverer(300, "")
        self._collect(d.deliver(_msg(evt), _caps(evt), None, "img.png", "羽镜",
                                ["过去", "现在", "未来"], [PICK] * 3,
                                _plain_text,
                                fail_note="📖 解读：" + chr(10) + "（AI 今天闹脾气）",
                                preface="今天安心"))
        kinds = [type(c).__name__ for c in evt.result]
        assert kinds == ["Image", "Plain", "Plain"]
        assert evt.result[1].text == "今天安心"
        assert evt.result[2].text.startswith("📖")
        assert not any("🔮 牌阵" in getattr(c, "text", "") for c in evt.result)


class TestInterpretationReuse:
    """AI 返回时已经解析过结构：发送层不再用正则重新切一遍。"""

    def test_structured_interpretation_skips_resplitting(self, monkeypatch):
        from data.plugins.astrbot_plugin_star_feather.adapters import (
            deliver as deliver_mod,
        )
        from data.plugins.astrbot_plugin_star_feather.ai.parse import (
            parse_interpretation,
        )

        def no_split(*a, **kw):
            raise AssertionError("结构化解读不该再走 split_sections")

        monkeypatch.setattr(deliver_mod, "split_sections", no_split)
        evt = types.SimpleNamespace()
        evt.result = None
        evt.get_self_id = lambda: "0"
        evt.chain_result = lambda chain: chain
        interp = parse_interpretation("【第1张·过去】一段。\n【总结】结论。")

        async def run():
            return [x async for x in Deliverer(300, "").deliver(
                _msg(evt), _caps(evt), interp, None, "羽时三刻", ["过去"], [PICK], lambda *a: "兜底")]

        parts = asyncio.run(run())
        text = "".join(getattr(c, "text", "") for c in parts[0])
        assert "一段。" in text and "结论。" in text
