"""星羽塔罗限流：命令节流与每日次数计数的纯逻辑。

设计原则（与 services/gating.py 一致）：
- 判定全部为纯函数，可单测；KV 读写由 services/gating.py 侧 try-except 静默降级
  （宁可不限流，也不能让占卜入口报错）。
- 键按「单 key 覆盖式」设计：数据随时间/日期自然失效，零清理任务。
- 会话级节流（sf_cmd_cd_*）与用户级每日计数（sf_cmd_cnt_*）维度不同，
  各自独立判定，互不干扰。

说人话：牌灵不小气，但也经不起连刷——规矩就这三条，记好了。
"""
import math


def cooldown_remaining(last_ts: float, now: float, cooldown: int) -> int:
    """短间隔节流的剩余秒数；未启用（cooldown<=0）或无记录（last_ts<=0）返回 0。

    自然语言入口（llm_tool_cooldown）与命令入口（cmd_rate_limit）共用此判定。
    """
    if cooldown <= 0 or last_ts <= 0:
        return 0
    if now < last_ts:
        # 服务器时钟被回拨：把负的 elapsed 当成「已过期」，别让用户被冻结
        # cooldown + 回拨量那么久。KV 里存的是墙钟时间戳，这里只做防御性归一。
        return 0
    # 向上取整：没真正走完就至少报 1 秒，别让 int() 把 0.8 秒截成 0 提前放行
    remain = math.ceil(cooldown - (now - last_ts))
    return max(0, remain)


def daily_remaining(limit: int, data, today: str) -> int:
    """每日次数限流的剩余次数；limit<=0 视为未启用返回 -1，调用方直接放行。

    data 为 KV 中读到的 {date, count}；跨天、缺数据、数据损坏一律按「无记录」
    处理（返回 limit），绝不因坏数据误伤用户。
    """
    if limit <= 0:
        return -1
    if not isinstance(data, dict) or data.get("date") != today:
        return limit
    try:
        count = int(data.get("count", 0))
    except (TypeError, ValueError, OverflowError):
        return limit
    # 计数不可能为负：坏记录归一为 0，别让「limit - 负数」反而放大配额
    return max(0, limit - max(0, count))


def daily_touch(data, today: str) -> dict:
    """计数 +1（跨天自动重置），返回可直接写回 KV 的 {date, count}。"""
    if isinstance(data, dict) and data.get("date") == today:
        try:
            count = int(data.get("count", 0))
        except (TypeError, ValueError, OverflowError):
            count = 0
    else:
        count = 0
    # 负数记录在本次请求后直接回到 1，不留在负值上继续累加
    return {"date": today, "count": max(0, count) + 1}
