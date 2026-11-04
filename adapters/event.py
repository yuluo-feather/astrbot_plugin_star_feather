"""AstrBot 事件适配：原始事件 → 中立 DTO / 投递能力 / 宿主动作。

应用层不再读框架事件；身份解析（含 int 型 ID 与平台前缀的二级降级）也在这里完成。
字段取值口径见 docs/design-notes/host-decoupling.md 阶段二。
"""
from ..core.dto import DeliveryCapabilities, IncomingEvent
from ..core.identity import escape_identity_part
from .deliver import supports_forward


def raw_sender_id(event) -> str:
    """直读原始消息的 sender.user_id：get_sender_id 封装异常时的后备来源。"""
    try:
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        return str(getattr(sender, "user_id", "") or "")
    except Exception:
        return ""


def platform_id(event) -> str:
    """事件所属平台适配器的 ID；框架保证多个同类适配器之间也唯一。

    取不到（测试桩 / 老版本事件）返回空串——调用方退回裸用户 ID，
    别让一个缺失的平台字段把身份整条弄丢。
    """
    try:
        return str(event.get_platform_id() or "").strip()
    except Exception:
        return ""


def resolve_sender_uid(event) -> str:
    """发送者用户标识，两级降级（任一拿到非空即用，纯函数可测）：

    1. event.get_sender_id()     —— 官方方法
    2. raw_sender_id(event)      —— 直读 sender.user_id（兼容 int 型 ID：
       官方 get_sender_id 只认 str，平台适配器给 int 会静默拿空，这里兜住）

    带平台前缀：不同适配器下相同的 user_id 不能共享今日牌运、牌灵的话与每日次数。
    两段里的 `%` / `:` 先转义，保证拼接无歧义；取不到平台 ID 时退回裸 uid。

    刻意不用 get_session_id / unified_msg_origin 兜底：群聊场景两者都是群号，
    会导致同群所有成员共享同一标识。全空返回 ""（调用方自行决定处理）。
    """
    candidates = (
        lambda: event.get_sender_id(),
        lambda: raw_sender_id(event),
    )
    for getter in candidates:
        try:
            uid = str(getter() or "").strip()
        except Exception:
            continue
        if uid:
            pid = platform_id(event)
            return (f"{escape_identity_part(pid)}:{escape_identity_part(uid)}"
                    if pid else escape_identity_part(uid))
    return ""


def platform_name(event) -> str:
    """平台名原值；取不到按空串（合并转发白名单会因此回退普通消息）。"""
    try:
        return str(event.get_platform_name() or "")
    except Exception:
        return ""


def self_id(event) -> str:
    """转发节点 uin：取框架 get_self_id()，取不到兜底 "0"（现状口径）。"""
    try:
        if hasattr(event, "get_self_id"):
            return str(event.get_self_id() or "0")
    except Exception:
        pass
    return "0"


def incoming_from_event(event) -> IncomingEvent:
    """把框架事件转成中立输入；身份在这里解析完，应用层不再碰原始事件。"""
    return IncomingEvent(
        user_id=resolve_sender_uid(event),
        platform_id=platform_id(event),
        conversation_id=getattr(event, "unified_msg_origin", None),
        message_text=getattr(event, "message_str", "") or "",
        platform_name=platform_name(event),
    )


def delivery_capabilities(event, forward_result: bool) -> DeliveryCapabilities:
    """本次投递能力：send_mode 开了 + 平台白名单才算能合并转发。"""
    return DeliveryCapabilities(
        can_forward=bool(forward_result) and supports_forward(platform_name(event)),
        self_id=self_id(event),
    )


def mark_handled(event) -> None:
    """本次事件已由插件处理：should_call_llm(True) 的语义是「禁止默认 LLM」。"""
    event.should_call_llm(True)
