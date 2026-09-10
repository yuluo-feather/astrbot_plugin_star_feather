"""限流闸门：会话节流与每日计数的 KV 粘合层（纯逻辑在 limiter.py）。

main.py 只做入口编排：「读写 KV + 生成拦截文案」收敛于此。
设计原则（与 limiter.py 一致）：
- 判定纯逻辑在 limiter（可单测）；本层只做 KV 读写与降级裁定——读写经 kv_utils
  安全层（吞异常+打日志，区分存储故障与无记录），本层按 ok 值决定放行还是计数
  （宁可不限流，也不能让占卜入口报错）。
- 键按「单 key 覆盖式」设计：数据随时间/日期自然失效，零清理任务。
- 会话级节流（sf_cmd_cd_*）与用户级每日计数（sf_cmd_cnt_*）维度不同，
  各自独立判定，互不干扰。

说人话：想连刷？牌灵会累的——但牌灵也懂体谅，存储挂了就放行。
"""
import asyncio
import logging
import time

from identity import resolve_sender_uid
from kv_utils import kv_get, kv_put
from limiter import cooldown_remaining, daily_remaining, daily_touch

logger = logging.getLogger(__name__)


class LimitGate:
    """限流闸门：供命令入口与工具入口共用。

    kv_store 须提供 get_kv_data/put_kv_data（插件实例自身即可）；
    cmd_rate_limit / daily_count_limit 来自 TarotSettings 解析结果。
    """

    def __init__(self, kv_store, cmd_rate_limit: int, daily_count_limit: int):
        self.kv_store = kv_store
        self.cmd_rate_limit = cmd_rate_limit
        self.daily_count_limit = daily_count_limit
        # 读-判-写原子化（2026-09-06 渗透实测）：真实 KV 每次 IO 都有事件循环让出点，
        # 并发请求会全部读到旧值 → 每日计数与会话节流被整体绕过（带 IO 延迟桩实测
        # 30 并发/配额5 全部放行、20 并发/冷却10s 全绕过）。实例级 asyncio.Lock
        # 串行化「读→判→写」窗口：单进程内原子（AstrBot 单体运行）；多进程部署
        # 需框架侧分布式锁，此锁不覆盖。KV 异常降级语义不变（锁只在正常路径持有）。
        self._kv_lock = asyncio.Lock()

    async def session_throttle(self, key: str, cooldown: int) -> int:
        """会话级节流（命令/工具入口共用）：返回剩余冷却秒（0=放行并可继续）。

        KV 读写异常静默放行——宁可不限流，也不能把占卜入口拦坏。
        命令入口与工具入口的冷却维度不同，key 各自独立，互不干扰。
        记录时间戳即可，无需会话标识（key 已含 umo 前缀区分维度）。
        """
        if cooldown <= 0:
            return 0
        async with self._kv_lock:  # 读-判-写原子化，防并发读旧值绕过冷却（渗透实测）
            last_ts, _ok = await kv_get(self.kv_store, key, 0, "会话节流")
            try:
                last_ts = float(last_ts or 0)
            except (TypeError, ValueError):
                last_ts = 0.0  # 坏数据当「没有记录」：放行，别把入口拦成「这场断了」
            remain = cooldown_remaining(last_ts, time.time(), cooldown)
            if remain > 0:
                return remain
            await kv_put(self.kv_store, key, int(time.time()), "会话节流")
            return 0

    async def check(self, event, for_command: bool) -> str | None:
        """限流闸门（三入口共用）：返回 None 放行，返回文案表示拦截本次占卜。

        - 命令入口会话级节流（仅 for_command=True 时查，防连点刷屏）：
          sf_cmd_cd_{消息源} 存上次时间戳，cmd_rate_limit 秒内不重复放行；
        - 每用户每日次数（三入口统一查，防 AI 解读被刷爆 token）：
          sf_cmd_cnt_{用户ID} 存 {date, count}，按日期判失效、跨天自动重置。

        KV 异常一律静默放行（宁可不限流，不能拦坏占卜入口）；uid 取不到时
        跳过每日计数（宁放过不误伤）。读-判-写经实例级 asyncio.Lock 串行化
        （单进程内原子，多进程部署不覆盖，见 __init__ 注释）；KV 异常降级放行
        语义不变。
        """
        # ① 命令入口会话级节流
        if for_command:
            umo = (getattr(event, "unified_msg_origin", None) or "global")
            remain = await self.session_throttle(f"sf_cmd_cd_{umo}", self.cmd_rate_limit)
            if remain > 0:
                # 会话级节流按消息源（群/私聊）维度：群里别人刚问过，等于整个会话歇口气，
                # 文案别把「为别人算」套在用户头上，用中性的“刚忙完，歇口气”。
                return f"牌灵刚忙完一卦，让它歇 {remain} 秒再来问~"
        # ② 每用户每日次数（三入口统一）
        if self.daily_count_limit > 0:
            uid = resolve_sender_uid(event)
            if uid:
                today = time.strftime("%Y%m%d")
                key = f"sf_cmd_cnt_{uid}"
                async with self._kv_lock:  # 读-判-写原子化，防并发读旧值超发配额（渗透实测）
                    data, ok = await kv_get(self.kv_store, key, None, "每日计数")
                    data = (data or {}) if ok else None  # 读失败：放行且不计数
                    if data is not None:
                        remain = daily_remaining(self.daily_count_limit, data, today)
                        if remain == 0:
                            return (f"今天已经问过牌灵 {self.daily_count_limit} 次啦~ "
                                    "明天零点牌运刷新后再来。")
                        if remain > 0:
                            await kv_put(self.kv_store, key, daily_touch(data, today), "每日计数")
        return None
