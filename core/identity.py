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


def platform_id(evt) -> str:
    """事件所属平台适配器的 ID；框架保证多个同类适配器之间也唯一。

    取不到（测试桩 / 老版本事件）返回空串——调用方退回裸用户 ID，
    别让一个缺失的平台字段把身份整条弄丢。
    """
    try:
        return str(evt.get_platform_id() or "").strip()
    except Exception:
        return ""


def _escape_part(part: str) -> str:
    """把平台 ID / 用户 ID 编成可安全拼进单键的片段。

    只转义 ``%`` 与 ``:``：前者是转义符本身，后者是分隔符。普通 ID（纯数字、
    ``qq`` / ``telegram`` 这类平台名）原样保留，键形仍是 ``platform_id:uid``；
    含分隔符的 ID 变成 ``%3A``，拼接因此是单射——``p`` + ``x:y`` 与 ``p:x`` + ``y``
    不会再落到同一个身份上（否则两个用户会共享今日牌运与每日次数）。
    """
    return part.replace("%", "%25").replace(":", "%3A")


def resolve_sender_uid(evt) -> str:
    """发送者用户标识，两级降级（任一拿到非空即用，纯函数可测）：

    1. evt.get_sender_id()     —— 官方方法
    2. raw_sender_id(evt)      —— 直读 sender.user_id（兼容 int 型 ID：
       官方 get_sender_id 只认 str，平台适配器给 int 会静默拿空，这里兜住）

    **带平台前缀**（2026-10-17 复核）：不同适配器下相同的 user_id（两个平台
    各有一个 123）从前会共享今日牌运、牌灵的话与每日次数。现在返回
    ``platform_id:uid``（两段里的 ``%`` / ``:`` 先转义，保证拼接无歧义）；
    取不到平台 ID 时退回裸 uid（测试桩与降级路径）。
    升级当天按用户维度的缓存会整体失效一次（`_daily_pick` 的种子也变了）。

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
            pid = platform_id(evt)
            return f"{_escape_part(pid)}:{_escape_part(uid)}" if pid else _escape_part(uid)
    return ""
