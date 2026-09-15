"""事件身份标识：从消息事件中提取发送者用户标识（两级降级，纯函数可测）。

daily（今日牌运）与 gating（每日限流）共用此层——「同一用户」的判定
依据统一，不各自实现、不互相依赖。
（说白了：牌灵要认人，认人的规矩只写一遍。）
"""


def raw_sender_id(event) -> str:
    """直读原始消息的 sender.user_id：get_sender_id 封装异常时的后备来源。"""
    try:
        sender = getattr(getattr(event, "message_obj", None), "sender", None)
        return str(getattr(sender, "user_id", "") or "")
    except Exception:
        return ""


def resolve_sender_uid(evt) -> str:
    """发送者用户标识，两级降级（任一拿到非空即用，纯函数可测）：

    1. evt.get_sender_id()     —— 官方方法
    2. raw_sender_id(evt)      —— 直读 sender.user_id（兼容 int 型 ID：
       官方 get_sender_id 只认 str，平台适配器给 int 会静默拿空，这里兜住）

    刻意不用 get_session_id / unified_msg_origin 兜底：群聊场景两者都是群号，
    会导致同群所有成员共享同一标识。全空返回 ""（调用方自行决定处理）。
    """
    candidates = (
        lambda: evt.get_sender_id(),
        lambda: raw_sender_id(evt),
    )
    for getter in candidates:
        try:
            uid = str(getter() or "").strip()
        except Exception:
            continue
        if uid:
            return uid
    return ""
