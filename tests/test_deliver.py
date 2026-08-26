"""发送层单测：长度分段 / 标记分段 / Deliverer 分发（无收尾参数）。
——结果怎么递到你手上，这里说了算。"""
import asyncio
import types

from deliver import Deliverer, split_sections, split_text
from stubs import PICK


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
