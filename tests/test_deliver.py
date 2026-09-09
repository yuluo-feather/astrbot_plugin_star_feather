"""发送层单测：长度分段 / 标记分段 / Deliverer 分发（无收尾参数）。
——结果怎么递到你手上，这里说了算。"""
import asyncio
import types

from stubs import PICK

from deliver import Deliverer, split_sections, split_text


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
    """Deliverer 不再承接收尾句（收尾改由 _run_reading 独立直发 / LLM 回复承担），
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
        return e

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]
        return asyncio.run(run())

    def test_no_epilogue_unchanged(self):
        evt = self._evt()
        d = Deliverer(300, "", False)
        self._collect(d.deliver(evt, "【总结】二", None,
                                "羽时三刻", ["过去"], [PICK], lambda *a: "兜底"))
        assert all(getattr(c, "text", "") != "✨ 收尾句" for c in evt.result)

    def test_message_order_image_then_preface_then_sections(self):
        """呈现顺序契约：牌面 → 牌灵的话(preface 裸句无前缀无引号) → 解读段 → 总结 → 免责。"""
        evt = self._evt()
        d = Deliverer(300, "免责声明", False)
        self._collect(d.deliver(evt, "【第1张·过去】一【总结】二", "img.png",
                                "羽时三刻", ["过去"], [PICK], lambda *a: "兜底",
                                preface="今天安心"))
        kinds = [type(c).__name__ for c in evt.result]
        assert kinds == ["Image", "Plain", "Plain", "Plain", "Plain"]
        assert evt.result[1].text == "今天安心\n"  # chain 模式 preface 带尾随换行
        assert "免责声明" in evt.result[-1].text

    def test_message_order_image_then_preface_forward(self):
        """合并转发模式同序：牌面节点在前，牌灵的话次之，最后免责。"""
        evt = self._evt()
        d = Deliverer(300, "免责声明", True)
        self._collect(d.deliver(evt, "【总结】二", "img.png",
                                "羽时三刻", ["过去"], [PICK], lambda *a: "兜底",
                                preface="今天安心"))
        nodes = evt.result[0].nodes
        assert "✨ 星羽塔罗 · 牌面" in nodes[0].content[1].text
        assert nodes[1].content[0].text == "今天安心"
        assert "免责声明" in nodes[-1].content[0].text


def _plain_text(formation, positions, picks):
    """仿 tarot_core._render_text 的逐牌牌义兜底文案（回归用例用）。"""
    NL = chr(10)
    lines = [f"🔮 牌阵：{formation}"]
    for i, (pos, pick) in enumerate(zip(positions, picks), 1):
        card = pick["card"]
        state = "正位" if pick["upright"] else "逆位"
        meaning = card[4] if pick["upright"] else card[5]
        lines.append("".join([f"🃏 第{i}张 ·【{pos}】", NL,
                              f"「{card[2]}」{state}", NL + "   ", meaning]))
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
        return e

    @staticmethod
    def _collect(agen):
        async def run():
            return [x async for x in agen]

        return asyncio.run(run())

    def test_no_image_preface_nonempty_keeps_per_card_meanings(self):
        evt = self._evt()
        d = Deliverer(300, "", False)
        positions = ["过去", "现在", "未来"]
        self._collect(d.deliver(evt, None, None, "羽镜", positions, [PICK] * 3,
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
        d = Deliverer(300, "", False)
        self._collect(d.deliver(evt, None, "img.png", "羽镜",
                                ["过去", "现在", "未来"], [PICK] * 3,
                                _plain_text,
                                fail_note="📖 解读：" + chr(10) + "（AI 今天闹脾气）",
                                preface="今天安心"))
        kinds = [type(c).__name__ for c in evt.result]
        assert kinds == ["Image", "Plain", "Plain"]
        assert evt.result[1].text == "今天安心"
        assert evt.result[2].text.startswith("📖")
        assert not any("🔮 牌阵" in getattr(c, "text", "") for c in evt.result)
