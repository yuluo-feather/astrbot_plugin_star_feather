"""今日固定牌运：运势词判定、确定性抽牌与当日缓存。

用户标识解析在 identity.py（daily 与限流共用，不各自实现）；
设计原则：
- 同一（用户, 日期）永远同一张牌：_daily_pick 是确定性纯函数（md5 种子），
  KV 缓存异常也不丢固定（缓存只做加速与解读复用）。
- 缓存「单 key 覆盖式」按日期判失效，零清理任务。
- KV 读写全部 try-except 静默降级：任何存储故障不阻塞占卜主流程。

说人话：今天这张牌，本羽说了算——问一百遍也是它，别想改命。
"""
import hashlib
import logging
import random
import time

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
TIME_WORDS = ("今天", "今日", "每日", "最近", "近期")
SPECIFIC_WORDS = (
    "感情", "爱情", "恋爱", "喜欢", "复合", "分手", "桃花", "关系",
    "事业", "工作", "学业", "考试", "考研", "面试", "升职",
    "他", "她", "我们",
)


def _is_daily_request(text: str) -> bool:
    """判断是否为「今日牌运」类请求：明确运势词即算；泛时间词须无具体主题。

    「今天她理我吗」是具体问题（旧注释同旨），「最近她对我什么感觉」同理；
    「最近怎么样」这类无主题泛问则视为当日牌运。
    """
    text = (text or "").lower()
    if any(w in text for w in DAILY_WORDS):
        return True
    if any(w in text for w in TIME_WORDS):
        return not any(w in text for w in SPECIFIC_WORDS)
    return False


def _daily_pick(uid: str, date_str: str) -> tuple:
    """同一（用户, 日期）永远返回同一张牌与正逆位。

    用 md5(日期:用户) 做种子、独立 random.Random 实例——跨进程一致、
    重启不变，且不碰全局 random，不影响 /占卜 的自由随机；
    正逆位同样由种子决定，纯随机、不可刷。
    """
    seed = hashlib.md5(f"{date_str}:{uid}".encode()).hexdigest()
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

    async def pick_cached(self, uid: str) -> tuple | None:
        """今日固定抽牌：单 key 覆盖式缓存（按日期判失效，无垃圾积累、零清理任务）。
        返回 (formation, positions, picks)；uid 为空时返回 None，
        由调用方统一回退自由随机（确定性函数保证 KV 异常也不丢固定）。"""
        if not uid:
            return None
        key, today = f"sf_daily_{uid}", time.strftime("%Y%m%d")
        data = {}
        try:
            data = await self.kv_store.get_kv_data(key, None) or {}
            if isinstance(data, dict) and data.get("date") == today:
                card, upright = data.get("card"), data.get("upright")
                if card in TAROT_CARDS and isinstance(upright, bool):
                    return _daily_result(card, upright)
        except Exception as e:
            logger.warning(f"每日牌运缓存读取失败，改由确定性函数直接出牌: {e}")
        # 缓存坏掉不丢固定：_daily_pick 是确定性纯函数，同（用户,日期）必然同牌
        card, upright = _daily_pick(uid, today)
        try:
            # 合并写而非覆盖：保留可能已存在的 interps（缓存不变量：
            # 「card/upright 恒存在」——即使解读先被写入、抽牌后重置也不丢弃）
            merged = dict(data) if isinstance(data, dict) else {}
            merged.update({"date": today, "card": card, "upright": upright})
            if not isinstance(data, dict) or data.get("date") != today:
                merged.pop("interps", None)  # 跨天：旧解读分桶作废
            await self.kv_store.put_kv_data(key, merged)
        except Exception:
            logger.warning("每日牌运缓存写入失败，本次仍返回固定牌")
        return _daily_result(card, upright)

    async def interp_cached(self, event, uid: str, formation: str, positions: list[str],
                            picks: list[dict], clean: str) -> str | None:
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
                event, formation, positions, picks, clean or "（今日牌运）")
        topic = _norm_topic(clean)
        key, today = f"sf_daily_{uid}", time.strftime("%Y%m%d")
        try:
            data = await self.kv_store.get_kv_data(key, None) or {}
            slots = data.get("interps") if isinstance(data, dict) else None
            if isinstance(data, dict) and data.get("date") == today \
                    and isinstance(slots, dict) and slots.get(topic):
                return slots[topic]
        except Exception:
            pass
        interp = await self.tarot._ai_interpret(
            event, formation, positions, picks, clean or "（今日牌运）")
        try:
            data = dict(await self.kv_store.get_kv_data(key, None) or {})
            slots = dict(data.get("interps") or {}) if isinstance(data.get("interps"), dict) else {}
            data["date"], data["interps"] = today, slots
            data["interps"][topic] = interp
            await self.kv_store.put_kv_data(key, data)
        except Exception:
            pass
        return interp
