"""测试共享桩：provider / context 模拟与常用牌常量（供 test_core / test_hardening 等复用）。

说白了就是给牌灵搭几个假对手：Provider 会哭会闹（抛异常/装死），Context 假装是 AstrBot 本人。
"""
import asyncio
import types


class FakeProvider:
    """模拟 AstrBot LLM provider：可控异常与延时，记录调用次数。"""

    def __init__(self, pid, result="解读文本", exc=None, delay=0.0):
        self.pid, self.result, self.exc, self.delay = pid, result, exc, delay
        self.calls = 0
        self.last_prompt = None

    def meta(self):
        return types.SimpleNamespace(id=self.pid)

    async def text_chat(self, **kwargs):
        self.calls += 1
        self.last_prompt = kwargs.get("prompt")
        self.last_system_prompt = kwargs.get("system_prompt")
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return types.SimpleNamespace(completion_text=self.result)


class FakeContext:
    """模拟 AstrBot Context：using 是当前会话/默认 provider，others 是全量列表。"""

    def __init__(self, using=None, others=None):
        self.using, self.others = using, others or []

    def get_using_provider(self, umo=None):
        return self.using

    def get_all_providers(self):
        return self.others


CARD = ("major", "0", "愚者", "The Fool", "新的开始", "冲动冒险")
PICK = {"card": CARD, "upright": True}
EVENT = types.SimpleNamespace(unified_msg_origin="test_umo")
# 结构合格的标准 AI 解读样本（【第N张·位置】+【总结】），供需要「正常产出」的用例复用
OK_INTERP = "【第1张·你的当下】新的开始，走出舒适区。【总结】大胆行动。"
