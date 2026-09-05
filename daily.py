"""今日固定牌运：运势词判定、确定性抽牌与当日缓存。

用户标识解析在 identity.py（daily 与限流共用，不各自实现）；
设计原则：
- 同一（用户, 日期）永远同一张牌：_daily_pick 是确定性纯函数（md5 种子），
  KV 缓存异常也不丢固定（缓存只做加速与解读复用）。
- 缓存「单 key 覆盖式」按日期判失效，零清理任务。
- KV 读写全部 try-except 静默降级：任何存储故障不阻塞占卜主流程。

说人话：今天这张牌，本羽说了算——问一百遍也是它，别想改命。
"""
import asyncio
import hashlib
import logging
import random
import re
import time
from datetime import datetime

from card_render import _render_daily_card_img, _schedule_image_cleanup
from dailylines import pick_signature
from kv_utils import kv_get, kv_put
from prompts import SPIRIT_PROMPT_V
from tarot_data import TAROT_CARDS

logger = logging.getLogger(__name__)

# 今日固定牌运判定：只有「明确在问当日运」才走每日固定，分两层——
# 1) 明确运势词（运势/运气/牌运/日运/daily）直接命中；
# 2) 泛时间词（今天/今日/每日/最近/近期）仅当整句不含具体主题词才算。
# 踩坑记录：旧版把「最近/近期/每日」直接当运势词，导致「最近她对我什么感觉」
# 「我最近学业怎么样」这类具体问题全被吞进每日固定牌——不管问什么都拿
# 到同一张牌、同一段解读。具体主题词表（SPECIFIC_WORDS）同时用于
# 解读缓存的「泛问/具体」归一化（见 _norm_topic），一处词表两处用。
DAILY_WORDS = ("运势", "运气", "牌运", "日运", "daily")
TIME_WORDS = ("今天", "今日", "每日", "最近", "近期",
              "这个月", "本月", "这两天", "这段时间")
SPECIFIC_WORDS = (
    "感情", "爱情", "恋爱", "喜欢", "复合", "分手", "桃花", "关系",
    "事业", "工作", "学业", "考试", "考研", "面试", "升职",
    "他", "她", "我们",
)
# 事件归因式排除：因果连词（句式类，非事件词枚举）——「运势/运气」出现在这些
# 连词之后时，它是「事件是不是运势导致」的归因宾语，不是被查询的运势值本身。
# 真·运势查询的「运势」在主题位（句首/句尾），位于因果连词之前，不命中排除。
CAUSE_WORDS = ("是因为", "是不是因为", "难道是因为", "就是因为",
               "可能因为", "或许因为是")

# 泛时间词分支的「正向白名单」两类（2026-08-29 由黑名单大漏斗收紧而来）：
# a) 泛问句式——「最近怎么样 / 这个月如何」是在问运势；
# b) 签文类——「每日塔罗 / 今天来一签」语义就是当日牌运；
# c) 其余（事件/日程/状态描述：最近总是失眠、今天下午开会吗、今天真的好累）
#    → 自由随机，出针对性解读——旧规则「时间词 + 无主题词即牌运」全把它们吞了。
_GENERIC_ASK_RE = re.compile(r"(怎么样|如何|还好吗|咋样|怎样|如何了)")
_SIGN_WORDS = ("塔罗", "一签", "签文")


def _is_event_attribution(text: str) -> bool:
    """事件归因式？——「运势/运气」等词出现在因果连词之后（作归因宾语）。

    例：「被门夹了，是因为运势太差吗」→ 是（用户在问具体事件的归因，
    不是查询运势值，走自由随机出针对性解读）；
    「今天运势为什么这么差」→ 否（运势词在句首主题位，「为什么」是追问）。
    """
    for cause in CAUSE_WORDS:
        i = text.find(cause)
        if i < 0:
            continue
        if any(w in text[i:] for w in DAILY_WORDS):
            return True
    return False


def _is_daily_request(text: str) -> bool:
    """判断是否为「今日牌运」类请求，按类别判定（自上而下）：

    ① 事件归因式（最高优先）：运势词作因果宾语 → 走自由随机，不是运势查询；
    ② 明确运势词（运势/运气/牌运/日运/daily）：直接命中——「今天感情运势」
       这类主题+运势也算（牌固定，解读按主题分缓存）；
    ③ 泛时间词：仅两类白名单问法算——泛问句式（最近怎么样/这个月如何）与
       签文类（每日塔罗/今天来一签）；事件/日程/状态描述走自由随机。

    「今天她理我吗」是具体问题（旧注释同旨），「最近她对我什么感觉」同理；
    「最近怎么样」这类无主题泛问则视为当日牌运。
    """
    text = (text or "").lower()
    if _is_event_attribution(text):
        return False
    if any(w in text for w in DAILY_WORDS):
        return True
    if any(w in text for w in TIME_WORDS):
        if any(w in text for w in SPECIFIC_WORDS):
            return False
        return bool(_GENERIC_ASK_RE.search(text) or any(w in text for w in _SIGN_WORDS))
    return False


def _daily_pick(uid: str, date_str: str) -> tuple:
    """同一（用户, 日期）永远返回同一张牌与正逆位。

    用 md5(日期:用户) 做种子、独立 random.Random 实例——跨进程一致、
    重启不变，且不碰全局 random，不影响 /占卜 的自由随机；
    正逆位同样由种子决定，纯随机、不可刷。
    """
    seed = hashlib.md5(f"{date_str}:{uid}".encode()).hexdigest()  # nosec B324 非安全用途：仅作确定性随机种子
    rng = random.Random(seed)
    upright = rng.random() < 0.5
    return rng.choice(TAROT_CARDS), upright


def _daily_result(card, upright: bool) -> tuple:
    """今日固定牌的返回结构：固定羽签单张阵 + 抽中的牌。"""
    return ("羽签", ["你的当下"], [{"card": card, "upright": upright}])


_GENERIC_TOPIC = "（今日牌运）"


def _norm_topic(clean: str) -> str:
    """解读缓存的主题指纹：具体问题按清洗后原文；泛问（无具体主题词）统一为当日牌运。

    这样「看看今天的运势」「每日一签」这类泛问措辞当天共享同一段解读
    （防反复问刷版本），而「今天感情运势」「最近学业怎么样」各按主题
    缓存——不同问题的解读再也不能互相串用。
    """
    raw = (clean or "").strip().lower()
    if not raw or not any(w in raw for w in SPECIFIC_WORDS):
        return _GENERIC_TOPIC
    return raw


class DailyFortune:
    """今日固定牌运的缓存与判定：给入口层提供「当天固定牌 + 当天解读」。

    独立持有 kv_store 与 tarot（核心）：kv_store 须提供 get_kv_data/put_kv_data，
    应传插件实例自身（Star 基类混入 PluginKVStoreMixin）——AstrBot 的 Context
    没有这两个方法，传错会导致缓存全程静默失效。KV 异常时由确定性函数兜底；
    入口层（main.py）仅通过本类访问，不直接接触 KV 细节。
    """

    def __init__(self, kv_store, tarot):
        # kv_store 传插件自身（见类 docstring）、tarot 是 StarTarot 实例
        self.kv_store = kv_store
        self.tarot = tarot

    async def render_daily_card(self, topics: list[str], picks: list[dict], uid: str) -> str | None:
        """今日牌运卡海报编排：签文（确定性函数）+ 日期文本 + 线程池渲染 + 300s 清理。

        渲染失败一律返回 None——调用方回退普通牌面图，不拦占卜主流程；
        清理期 300 秒而非常规 30 秒：卡片是给人存图转发的，别转个身就没了。
        topics 保留接口（当前海报标题固定「星羽塔罗·今日牌运」，供日志与后续扩展）。
        """
        try:
            card = picks[0]["card"]
            upright = bool(picks[0]["upright"])
            date_text = (f"{int(time.strftime('%m'))}月{int(time.strftime('%d'))}日"
                         f"·周{'一二三四五六日'[datetime.now().weekday()]}")
            signature = pick_signature(card, upright, uid, time.strftime("%Y%m%d"))
            img = await asyncio.to_thread(_render_daily_card_img, card, upright, signature,
                                          date_text)
            if not img or not isinstance(img, str) or not img.strip():
                return None
            _schedule_image_cleanup(img, delay=300)
            return img
        except Exception as e:
            logger.warning(f"今日牌运卡渲染失败，回退普通牌面图: {e}")
            return None

    async def pick_cached(self, uid: str) -> tuple | None:
        """今日固定抽牌：单 key 覆盖式缓存（按日期判失效，无垃圾积累、零清理任务）。
        返回 (formation, positions, picks)；uid 为空时返回 None，
        由调用方统一回退自由随机（确定性函数保证 KV 异常也不丢固定）。"""
        if not uid:
            return None
        key, today = f"sf_daily_{uid}", time.strftime("%Y%m%d")
        data, ok = await kv_get(self.kv_store, key, None, "每日牌运缓存")
        if ok and isinstance(data, dict) and data.get("date") == today:
            card, upright = data.get("card"), data.get("upright")
            if card in TAROT_CARDS and isinstance(upright, bool):
                return _daily_result(card, upright)
        # 缓存坏掉不丢固定：_daily_pick 是确定性纯函数，同（用户,日期）必然同牌
        card, upright = _daily_pick(uid, today)
        if ok:
            # 只有正常读到（ok=True）才合并写回：读故障时不写（写回了也未必可靠、
            # 且旧实现同样跳过写回）；无记录时 data=None，合并从空壳起步即可
            # 合并写而非覆盖：保留可能已存在的 interps（缓存不变量：
            # 「card/upright 恒存在」——即使解读先被写入、抽牌后重置也不丢弃）
            merged = dict(data) if isinstance(data, dict) else {}
            merged.update({"date": today, "card": card, "upright": upright})
            if not isinstance(data, dict) or data.get("date") != today:
                merged.pop("interps", None)  # 跨天：旧解读分桶作废
            await kv_put(self.kv_store, key, merged, "每日牌运缓存")
        else:
            logger.warning("每日牌运缓存不可用，改由确定性函数直接出牌")
        return _daily_result(card, upright)

    async def interp_cached(self, event, uid: str, formation: str, positions: list[str],
                            picks: list[dict], clean: str,
                            persona_eff=None) -> str | None:
        """今日牌运的解读缓存：按主题指纹（_norm_topic）分桶缓存。

        泛问（无具体主题）统一归为当日牌运，当天固定同一段解读，防止
        「反复问运势」连解读都刷出不同版本；带主题（如「今天感情运势」
        「最近学业怎么样」）各按主题分桶——同主题复用、换主题现场生成、
        换回来不丢，不同问题再不串答案（旧版单槽无主题区分，同一天问什么
        都返回第一段解读——「问什么都一个答案」的元凶）。
        主题数受每日次数限流兜底，分桶大小有界，无增长风险。
        """
        if not uid:
            return await self.tarot._ai_interpret(
                event, formation, positions, picks, clean or "（今日牌运）",
                persona_eff=persona_eff)
        topic = _norm_topic(clean)
        key, today = f"sf_daily_{uid}", time.strftime("%Y%m%d")
        data, ok = await kv_get(self.kv_store, key, None, "每日牌运解读缓存")
        if ok and isinstance(data, dict) and data.get("date") == today:
            slots = data.get("interps") if isinstance(data, dict) else None
            if isinstance(slots, dict) and slots.get(topic):
                return slots[topic]
        interp = await self.tarot._ai_interpret(
            event, formation, positions, picks, clean or "（今日牌运）",
            persona_eff=persona_eff)
        if ok:
            # 仅正常读到才写回分桶：读故障时旧缓存保持原样（写回可能把有效数据
            # 覆盖成只含本主题的空壳），旧实现同样走 except 跳过写回
            data = dict(data or {})
            slots = dict(data.get("interps") or {}) if isinstance(data.get("interps"), dict) else {}
            data["date"], data["interps"] = today, slots
            data["interps"][topic] = interp
            await kv_put(self.kv_store, key, data, "每日牌运解读缓存")
        return interp

    async def spirit_cached(self, event, uid: str, picks: list, clean: str,
                            persona_eff) -> str:
        """牌灵的话：AI 按人设生成（贴合本次牌面+主题），当日同牌组缓存同一句；
        AI 失败回退池内当日签文（不写缓存，下次恢复后重新生成）。

        所有牌阵共用（每日牌运单张、普通占卜整签）：缓存键 sf_spirit_{uid}
        单 key 覆盖式，命中条件=当日+同牌组指纹（顺序+正逆），同人同日同牌组
        当天同一句、第二天换新的。与海报卡面分工：海报印池内固定句（卡面装饰）；
        聊天这句是牌灵的开口（人设化、每天新鲜一句）。
        """
        today = time.strftime("%Y%m%d")
        if not picks:
            return ""
        card, upright = picks[0]["card"], picks[0]["upright"]
        fallback = pick_signature(card, upright, uid, today)
        if not uid:
            return fallback  # 无标识：连缓存都无从谈起，直接池内兜底
        key = f"sf_spirit_{uid}"
        sig = "|".join(f"{p['card'][2]}:{1 if p['upright'] else 0}" for p in picks)
        data, ok = await kv_get(self.kv_store, key, None, "牌灵的话缓存")
        if ok and isinstance(data, dict) and data.get("v") == SPIRIT_PROMPT_V \
                and data.get("date") == today and data.get("sign") == sig \
                and data.get("line"):
            return data["line"]
        line = await self.tarot.interpreter.spirit_line(
            event, [(p["card"], p["upright"]) for p in picks],
            _norm_topic(clean), persona_eff)
        if not line:
            return fallback  # AI 失败：池内当日句；不写缓存，下次再试 AI
        await kv_put(self.kv_store, key, {"v": SPIRIT_PROMPT_V, "date": today,
                                          "sign": sig, "line": line},
                     "牌灵的话缓存")
        return line
