"""核心端口：纯逻辑只依赖这些 Protocol，不 import astrbot。

端口只描述「需要什么能力」，不描述「谁提供」。框架相关的实现放在
adapters/astrbot_io.py；测试可以直接给 fake。
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class KV(Protocol):
    """键值存储：get 返回 (值, 是否正常读到)，put 返回是否写入成功。

    把「存储故障」与「无记录」分开是调用点的降级语义需要——
    限流要前者放行且不计数，后者正常计数。
    """

    async def get(self, key: str, default: Any = None) -> tuple[Any, bool]: ...

    async def put(self, key: str, value: Any) -> bool: ...


@runtime_checkable
class Clock(Protocol):
    """日期边界：today() 给键，date_text() 给海报卡面。"""

    def today(self) -> str: ...

    def date_text(self) -> str: ...


@runtime_checkable
class Rng(Protocol):
    """抽牌随机源：只要 random() 与 sample() 两个能力。"""

    def random(self) -> float: ...

    def sample(self, population: Any, k: int) -> list: ...


@runtime_checkable
class Messenger(Protocol):
    """消息出口：文本与已构造好的结果对象，返回是否发送成功。"""

    async def send_text(self, text: str) -> bool: ...

    async def send_result(self, result: Any) -> bool: ...
