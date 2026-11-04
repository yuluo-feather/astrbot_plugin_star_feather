"""Provider 网关端口：解释器只依赖这四件事。

句柄是不透明对象——解释器不读它的字段，只把它交回网关。这样 `AiInterpreter`
不再知道 AstrBot 的 `Context`，换宿主时只换适配器。
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProviderGateway(Protocol):
    """模型来源与调用：取当前 / 全量句柄、认 id、发一次请求。"""

    async def current(self, umo: str | None) -> Any: ...

    def all(self) -> list[Any]: ...

    def identify(self, handle: Any) -> str: ...

    async def chat(self, handle: Any, prompt: str, system_prompt: str) -> str: ...
