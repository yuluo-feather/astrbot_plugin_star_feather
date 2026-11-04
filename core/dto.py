"""应用层的中立输入与投递能力：只描述「收到什么、能怎么发」，不含原始事件。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingEvent:
    """一次请求的中立输入；字段取值口径见 adapters/event.py 与任务书阶段二。"""

    user_id: str
    platform_id: str
    conversation_id: str | None
    message_text: str
    platform_name: str

    @property
    def conversation_key(self) -> str:
        """限流键用的会话标识：与原 `unified_msg_origin or "global"` 完全同义。"""
        return self.conversation_id or "global"


@dataclass(frozen=True)
class DeliveryCapabilities:
    """本次投递能力：要不要合并转发、转发节点用哪个 uin。"""

    can_forward: bool
    self_id: str
