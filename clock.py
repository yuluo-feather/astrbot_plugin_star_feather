"""统一时钟：项目里「今天是哪一天」只在这一处算。

为什么值得单独一层：日期是好几处键的切片依据（今日固定牌 `sf_daily_*`、
牌灵的话 `sf_spirit_*`、每日计数 `sf_cmd_cnt_*`），边界错一小时就是
「刚过零点的人拿到昨天的牌」或「昨天问过的解读算今天」。散着写 `time.strftime`
时，改一次口径要翻遍 daily 与 gating，还容易只改一半——本模块把
「用哪个时区、怎么取今天」收成一个对象，调用方只问 `Clock.today()`。

时区口径（2026-10-12，⑨）：**优先框架全局配置里的 timezone（IANA 名）**，
没配或名字非法时退回系统本地时区——与框架自己的读法同一条判据
（`astrbot/core/astr_main_agent.py` 的 `_append_system_reminders`：
`ZoneInfo(name)` 失败记一条错、改用本地）。刻意不在插件里硬写
`Asia/Shanghai`：容器与服务器若在框架里配了别的时区，硬写会让插件的「今天」
和框架的「今天」打架；要改默认值就去改框架那份配置（面板 · 配置文件 · timezone）。

说人话：牌灵的「今天」跟框架的「今天」必须是同一天，别各算各的。
"""
import datetime
from zoneinfo import ZoneInfo

from astrbot.api import logger

# 周中文名：日期文本用（「8月27日·周四」）
_WEEKDAY_CN = "一二三四五六日"


def resolve_timezone(context) -> tuple[datetime.tzinfo | None, str]:
    """读框架全局配置的 timezone，返回 (tzinfo, 原始名)。

    - 读不到 / 空值 → (None, "")：跟随系统本地时区（框架配置说明里就是这么写的：
      「为空时使用系统默认时区」）；
    - 名字非法（ZoneInfo 抛）→ 同样 (None, "")，但留一条 warning——静默退回本地
      会让「为什么日期边界不对」变成无从归因的疑案，而这条日志一句话就能定罪。

    读配置整段裹 try：这是诊断与日期口径的入口，不是占卜主流程——
    框架将来改了 get_config 的形状，退化成「跟随本地时区」远好过把插件打崩。
    """
    name = ""
    try:
        cfg = context.get_config()
        name = str((cfg or {}).get("timezone") or "").strip()
    except Exception as e:
        logger.warning(f"读取框架时区配置失败，日期边界改为跟随系统本地时区: {e}")
        return None, ""
    if not name:
        return None, ""
    try:
        return ZoneInfo(name), name
    except Exception as e:
        logger.warning(f"框架时区配置「{name}」无法解析（{e}），日期边界退回系统本地时区")
        return None, ""


class Clock:
    """日期边界的唯一来源：`today()` 给键，`date_text()` 给海报卡面上的日期。

    now_fn 可注入（测试造「刚过零点的两个时区」这类场景，不睡墙钟、也不动系统时钟）；
    不注入时按 tz 取当前时间：有 tz 就用它，没有就用系统本地时区
    （`.astimezone()` 明确带上本地时区，不拿 naive 时间去过日期边界——
    naive 的 weekday() 在跨时区部署里取决于宿主，正是本模块要消掉的那点不确定性）。
    """

    def __init__(self, tz: datetime.tzinfo | None = None, now_fn=None):
        self.tz = tz
        self._now_fn = now_fn or self._system_now

    def _system_now(self) -> datetime.datetime:
        if self.tz is not None:
            return datetime.datetime.now(self.tz)
        return datetime.datetime.now().astimezone()

    @classmethod
    def from_context(cls, context) -> "Clock":
        tz, _name = resolve_timezone(context)
        return cls(tz)

    def now(self) -> datetime.datetime:
        return self._now_fn()

    def today(self) -> str:
        """今日日期键：YYYYMMDD（今日牌运 / 牌灵的话 / 每日计数共用同一个值）。"""
        return self.now().strftime("%Y%m%d")

    def date_text(self) -> str:
        """海报卡面上的日期文本：「8月27日·周四」（月日不补零，与旧卡面一致）。"""
        n = self.now()
        return f"{n.month}月{n.day}日·周{_WEEKDAY_CN[n.weekday()]}"
