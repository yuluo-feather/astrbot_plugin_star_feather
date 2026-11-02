"""今日固定牌运：确定性抽牌与当日缓存（意图判定与主题分桶在 intent.py）。

用户标识由 identity.py 统一解析、由入口传入（daily 与限流共用同一口径，不各自实现）；
设计原则：
- 同一（用户, 日期）永远同一张牌：_daily_pick 是确定性纯函数（md5 种子），
  KV 缓存异常也不丢固定（缓存只做加速与解读复用）。
- 一个用户一条 DailyRecord（schema 2），牌、解读分桶与牌灵的话共用同一个日期边界。
- KV 读写全部 try-except 静默降级：任何存储故障不阻塞占卜主流程。

说人话：今天这张牌，本羽说了算——问一百遍也是它，别想改命。
"""
import asyncio
import hashlib
import random

from .ai.parse import Interpretation, parse_interpretation
from .ai.prompts import READING_PROMPT_V, SPIRIT_PROMPT_V
from .clock import Clock
from .core.draw import render_detail
from .core.log import log
from .dailylines import pick_signature
from .intent import norm_topic
from .kv_utils import kv_delete, kv_get, kv_put
from .services.daily_store import DailyRecord
from .services.singleflight import SingleFlight
from .settings import DEFAULT_AI_PERSONA
from .tarot_data import TAROT_CARDS

# 同键生成去重：模块级而非实例属性，热重载前后并存的实例也能共享同一笔生成。
_SPIRIT_FLIGHT = SingleFlight()
_INTERP_FLIGHT = SingleFlight()

# 提示词版本合成一个指纹：读数或牌灵提示词任一改版，当天缓存都失效。
_PROMPT_V = f"{READING_PROMPT_V}/{SPIRIT_PROMPT_V}"

# 今日固定牌运的意图判定与主题分桶已收进 intent.py：
# 本文件只管确定性抽牌与缓存，norm_topic 从那边导入，词表只有一份。


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


_CARDS_BY_ID = {c["id"]: c for c in TAROT_CARDS}

# 解读分桶上限：当天换主题不回收、跨天才随新记录作废，桶数会线性增长。
DAILY_INTERP_MAX = 20


def _trim_interp_slots(slots: dict) -> None:
    """把解读分桶裁到 DAILY_INTERP_MAX：超出的按「最早插入」逐个丢（原地改）。"""
    while len(slots) > DAILY_INTERP_MAX:
        slots.pop(next(iter(slots)), None)


def _reading_sign(picks: list[dict]) -> str:
    """解读缓存的牌组指纹：牌 id + 正逆，顺序敏感。"""
    return "|".join(f"{p['card']['id']}:{1 if p['upright'] else 0}" for p in picks)


def _spirit_sign(picks: list[dict]) -> str:
    """牌灵的话的牌组指纹：牌名 + 正逆（沿用旧口径）。"""
    return "|".join(f"{p['card']['cn']}:{1 if p['upright'] else 0}" for p in picks)


async def _ai_output(awaitable, what: str, who: str = ""):
    """装饰性 AI 产出的统一故障边界：异常 → None，走各自的降级路。"""
    try:
        return await awaitable
    except Exception as e:
        log.warning(f"{what}生成异常（{who or '无标识'}），走降级: {e}")
        return None


class DailyFortune:
    """今日固定牌运的缓存与判定：给入口层提供「当天固定牌 + 当天解读」。

    依赖全部显式注入：kv_store（须提供 get_kv_data/put_kv_data）、clock（日期边界）、
    persona（人设配置，缓存指纹用）、interpreter（AI 解释器）。
    """

    clock = Clock()

    def __init__(self, kv_store, clock=None, persona: str = DEFAULT_AI_PERSONA,
                 interpreter=None):
        self.kv_store = kv_store
        self.clock = clock or Clock()
        self.persona = str(persona or "")
        self.interpreter = interpreter
        # 旧版 sf_spirit_* 键只删一次：合并成单键后它们不会再被读，但会永久占着
        self._legacy_cleaned: set[str] = set()
        # 缓存读-判-写串行化（gating 同款防线）：并发占卜时两条协程各自读旧值再写回，
        # 后写的会把先写的分桶整个覆盖掉。
        self._kv_lock = asyncio.Lock()

    def _key(self, uid: str) -> str:
        return f"sf_daily_{uid}"

    async def _cleanup_legacy_spirit(self, uid: str) -> None:
        """删掉旧版独立的 sf_spirit_* 键（每个用户一次）。"""
        if not uid or uid in self._legacy_cleaned:
            return
        self._legacy_cleaned.add(uid)
        await kv_delete(self.kv_store, f"sf_spirit_{uid}", "旧牌灵缓存")

    async def pick_cached(self, uid: str) -> tuple | None:
        """今日固定抽牌：一个用户一条记录，按日期判失效。

        返回 (formation, positions, picks)；uid 为空时返回 None，
        由调用方统一回退自由随机（确定性函数保证 KV 异常也不丢固定）。
        """
        if not uid:
            return None
        await self._cleanup_legacy_spirit(uid)
        key, today = self._key(uid), self.clock.today()
        async with self._kv_lock:
            raw, ok = await kv_get(self.kv_store, key, None, "每日牌运缓存")
            record = DailyRecord.from_raw(raw)
            if ok and record.date == today:
                card = _CARDS_BY_ID.get(record.card_id)
                if card is not None:
                    return _daily_result(card, record.upright)
            # 缓存坏掉不丢固定：_daily_pick 是确定性纯函数，同（用户,日期）必然同牌
            card, upright = _daily_pick(uid, today)
            if ok:
                picks = [{"card": card, "upright": upright}]
                sig = _reading_sign(picks)
                keep = record.same_context(today, sig, _PROMPT_V, self.persona)
                fresh = record if keep else DailyRecord()
                fresh.put_pick(card["id"], upright, sig, today, _PROMPT_V, self.persona)
                if not keep:
                    fresh.interps, fresh.spirit_line, fresh.spirit_sign = {}, "", ""
                await kv_put(self.kv_store, key, fresh.to_raw(), "每日牌运缓存")
            else:
                log.warning("每日牌运缓存不可用，改由确定性函数直接出牌")
        return _daily_result(card, upright)

    async def interp_cached(self, umo: str | None, uid: str, formation: str,
                            positions: list[str], picks: list[dict], clean: str,
                            persona_eff=None, budget=None) -> Interpretation | None:
        """今日牌运的解读缓存：按主题指纹（norm_topic）分桶。

        命中要求 date + 牌组指纹 + prompt 指纹 + 人设配置一致；同一主题的并发 miss
        由 SingleFlight 去重，等待者拿同一段话。缓存命中只读不写。
        """
        topic = norm_topic(clean)
        key, today = self._key(uid), self.clock.today()
        sig = _reading_sign(picks)
        reg_key = (key, sig, topic, today, _PROMPT_V, self.persona)
        hit = None
        async with self._kv_lock:
            raw, ok = await kv_get(self.kv_store, key, None, "每日牌运解读缓存")
            record = DailyRecord.from_raw(raw)
            if ok and record.same_context(today, sig, _PROMPT_V, self.persona):
                hit = record.interps.get(topic)
        if hit is not None:
            return Interpretation.from_record(hit)

        async def generate():
            return await _ai_output(
                self.interpreter.interpret(
                    umo, formation, positions, picks, clean or "（今日牌运）",
                    render_detail(positions, picks),
                    persona_eff=persona_eff, budget=budget),
                "AI 解读", uid)

        interp = await _INTERP_FLIGHT.run(reg_key, generate, fallback=None)
        if not ok or interp is None:
            return interp

        async with self._kv_lock:
            fresh_raw, _ = await kv_get(self.kv_store, key, None, "每日牌运解读缓存")
            fresh = DailyRecord.from_raw(fresh_raw)
            # 生成跨过了午夜：KV 里已经是新一天的记录，别用昨天启动的写回盖掉
            if fresh.date > today:
                return interp
            if not fresh.same_context(today, sig, _PROMPT_V, self.persona):
                fresh = DailyRecord()
            fresh.put_context(today, sig, _PROMPT_V, self.persona)
            parsed = interp if isinstance(interp, Interpretation) else parse_interpretation(str(interp))
            if parsed is not None:
                fresh.interps[topic] = parsed.to_record()
            _trim_interp_slots(fresh.interps)
            await kv_put(self.kv_store, key, fresh.to_raw(), "每日牌运解读缓存")
        return interp

    async def spirit_cached(self, umo: str | None, uid: str, picks: list, topic: str,
                            persona_eff, budget=None) -> str:
        """牌灵的话：AI 按人设生成，当日同牌组缓存同一句；失败回退池内当日签文。

        与解读共用 `sf_daily_*` 一条记录：同一天同一牌组只有一句；读故障时不写回，
        避免在不知道旧记录的情况下把已写好的解读分桶覆盖成空壳。
        """
        today = self.clock.today()
        if not picks:
            return ""
        card, upright = picks[0]["card"], picks[0]["upright"]
        fallback = pick_signature(card, upright, uid, today)
        if not uid:
            return fallback
        await self._cleanup_legacy_spirit(uid)
        key, sig = self._key(uid), _spirit_sign(picks)
        reg_key = (key, sig, today, _PROMPT_V)

        async with self._kv_lock:
            raw, ok = await kv_get(self.kv_store, key, None, "牌灵的话缓存")
            record = DailyRecord.from_raw(raw)
            if (ok and record.date == today and record.prompt_v == _PROMPT_V
                    and record.spirit_sign == sig and record.spirit_line):
                return record.spirit_line

        async def generate():
            return await _ai_output(
                self.interpreter.spirit_line(
                    umo, [(p["card"], p["upright"]) for p in picks],
                    norm_topic(topic), persona_eff, budget=budget),
                "牌灵的话", uid)

        line = await _SPIRIT_FLIGHT.run(reg_key, generate, fallback=fallback)
        if not line:
            return fallback
        if not ok:
            # 读故障：只把这一句交给本次调用，不冒险写回（可能覆盖已有分桶）
            return line

        async with self._kv_lock:
            fresh_raw, _ = await kv_get(self.kv_store, key, None, "牌灵的话缓存")
            fresh = DailyRecord.from_raw(fresh_raw)
            if fresh.date > today:
                # 生成跨过了午夜：KV 已是新一天的记录，别用昨天启动的写回盖掉
                return line
            if (fresh.date == today and fresh.prompt_v == _PROMPT_V
                    and fresh.spirit_sign == sig and fresh.spirit_line):
                return fresh.spirit_line
            if fresh.date != today:
                fresh = DailyRecord()
            # 只写牌灵字段：普通占卜的牌灵写回不许碰今日固定牌的 card_id/upright
            fresh.put_spirit(line, sig, today, _PROMPT_V)
            await kv_put(self.kv_store, key, fresh.to_raw(), "牌灵的话缓存")
        return line
