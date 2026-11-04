"""核心端口：纯逻辑只依赖这些 Protocol，不 import astrbot。

端口只描述「需要什么能力」，不描述「谁提供」。框架相关的实现放在
adapters/astrbot_io.py；测试可以直接给 fake。
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """日期边界：today() 给键，date_text() 给海报卡面，snapshot() 一次取两者。"""

    def today(self) -> str: ...

    def date_text(self) -> str: ...

    def snapshot(self) -> tuple[str, str]: ...


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


@runtime_checkable
class ResultMessenger(Messenger, Protocol):
    """带宿主结果构造能力的消息出口；只在适配器内部使用。

    应用层只依赖 `Messenger` 的发送能力；`chain_result` 是宿主把中立链条
    组装成自己的消息对象那一步，不进入业务逻辑。
    """

    def chain_result(self, chain: Any) -> Any: ...
