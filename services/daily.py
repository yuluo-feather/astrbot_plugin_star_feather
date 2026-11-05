"""今日固定牌运：确定性抽牌与当日缓存（意图判定与主题分桶在 core/intent.py）。

用户标识由 core/identity.py 统一解析、由入口传入（daily 与限流共用同一口径，不各自实现）；
设计原则：
- 同一（用户, 日期）永远同一张牌：_daily_pick 是确定性纯函数（md5 种子），
  KV 缓存异常也不丢固定（缓存只做加速与解读复用）。
- 一个用户一条 DailyRecord（schema 2），牌、解读分桶与牌灵的话共用同一个日期边界。
- KV 读写全部 try-except 静默降级：任何存储故障不阻塞占卜主流程。
- 缓存结构与三条写回保护的来历见 docs/design-notes/daily-cache.md。

说人话：今天这张牌，本羽说了算——问一百遍也是它，别想改命。
"""
import asyncio
import hashlib
import random
import threading
import weakref

from ..adapters.clock import Clock
from ..adapters.kv_utils import kv_delete, kv_get, kv_put
from ..ai.parse import (
    interpretation_from_record,
    parse_interpretation,
    validate_interpret_structure_strict,
)
from ..ai.prompts import READING_PROMPT_V, SPIRIT_PROMPT_V
from ..core.dailylines import pick_signature
from ..core.draw import render_detail
from ..core.intent import norm_topic
from ..core.log import log
from ..core.models import Interpretation
from ..core.settings import (
    DEFAULT_AI_PERSONA,
    DEFAULT_STRUCTURE_CHECK,
    STRUCTURE_CHECK_STRICT,
)
from ..core.tarot_data import TAROT_CARDS
from .daily_store import DailyRecord
from .singleflight import SingleFlight

# 同键生成去重：模块级而非实例属性——同一代模块内的多个实例共享同一笔生成。
# 热重载会重建模块字典，新旧两代各持一份，互不共享（与 _KV_LOCKS 的边界一致）。
_SPIRIT_FLIGHT = SingleFlight()
_INTERP_FLIGHT = SingleFlight()

# 进程级写锁：同一代模块内所有 DailyFortune 共用，保护单键记录的「读—合并—写」；
# 热重载会重建模块，新旧两代互不共享。锁按事件循环隔离、键用弱引用，
# 取锁时清理已关闭循环的条目。边界与来历见 docs/design-notes/concurrency-and-locks.md。
_KV_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary())
_KV_LOCKS_GUARD = threading.Lock()


def _shared_kv_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    with _KV_LOCKS_GUARD:
        for known in [lp for lp in _KV_LOCKS if lp.is_closed()]:
            _KV_LOCKS.pop(known, None)
        lock = _KV_LOCKS.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            _KV_LOCKS[loop] = lock
        return lock

# 提示词版本合成一个指纹：读数或牌灵提示词任一改版，当天缓存都失效。
_PROMPT_V = f"{READING_PROMPT_V}/{SPIRIT_PROMPT_V}"

# 今日固定牌运的意图判定与主题分桶已收进 core/intent.py：
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

# 今日有牌运记录的用户名单：宿主 KV 只能按 key 读、不能枚举，
# 可视化视图要列「今天有谁抽过」只能自己留一份（只存 uid，不存内容）。
_INDEX_KEY = "sf_daily_index"
_INDEX_MAX = 500


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
    persona（人设配置，缓存指纹用）、interpreter（AI 解释器）、
    ai_enabled（ai.enable_ai：缓存命中同样受它约束）、
    structure_check（ai.structure_check：缓存命中同样按当前口径复检）。
    """

    def __init__(self, kv_store, clock=None, persona: str = DEFAULT_AI_PERSONA,
                 interpreter=None, ai_enabled: bool = True,
                 structure_check: str = DEFAULT_STRUCTURE_CHECK):
        self.kv_store = kv_store
        self.clock = clock or Clock()
        self.persona = str(persona or "")
        self.interpreter = interpreter
        self.ai_enabled = ai_enabled
        self.structure_check = structure_check
        # 旧版 sf_spirit_* 键只删一次：合并成单键后它们不会再被读，但会永久占着
        self._legacy_cleaned: set[str] = set()
        # 本进程今天已登记进日索引的用户：跨天清空，避免 (日期, 用户) 永久累积
        self._indexed: set[str] = set()
        self._indexed_day: str = ""

    @property
    def prompt_version(self) -> str:
        """当前解读/牌灵合成指纹（可视化视图拿它判断缓存是否过期）。"""
        return _PROMPT_V

    def _structure_ok(self, text: str, n_positions: int) -> bool:
        """缓存命中也复检结构：strict 下旧缓存不能绕过结构闸。

        只对 strict 复检：loose 是更宽的旧口径，缓存里可能还有升级前的纯文本记录，
        那是文档承诺可读的兼容路径；当次生成本来就经过了 loose 校验。
        """
        if self.structure_check != STRUCTURE_CHECK_STRICT:
            return True
        ok, why = validate_interpret_structure_strict(text, n_positions)
        if not ok:
            log.warning(
                f"每日牌运缓存结构不合严格口径（{'/'.join(why)}），按未命中重新生成")
        return ok

    def _key(self, uid: str) -> str:
        return f"sf_daily_{uid}"

    async def _note_index(self, uid: str, today: str) -> None:
        """把今天有牌运记录的用户记进日索引（调用方须持有 _shared_kv_lock）。

        索引坏了最多让可视化视图少列一个人，不影响牌运主流程。本进程内每个
        （日期, 用户）只成功写一次；跨天会重新登记。名单满员后淘汰最早登记的
        用户，被淘汰者同时从内存记账里移除，再次活跃时能重新进索引。读故障
        不知道旧名单，写回会把当天名单缩成一个人；写故障则留到下次调用再
        试——两种情况都不记账。
        """
        if not uid:
            return
        if self._indexed_day != today:
            self._indexed.clear()
            self._indexed_day = today
        if uid in self._indexed:
            return
        raw, ok = await kv_get(self.kv_store, _INDEX_KEY, None, "每日牌运用户索引")
        if not ok:
            return
        index = raw if isinstance(raw, dict) else {}
        index_date = index.get("date")
        if isinstance(index_date, str) and index_date > today:
            # 延迟到达的旧日期请求：索引已被推进到更晚一天，不能回退
            return
        uids = index.get("uids") if index_date == today else []
        known = [u for u in uids if isinstance(u, str)] if isinstance(uids, list) else []
        if uid not in known:
            known.append(uid)
            overflow = len(known) - _INDEX_MAX
            if overflow > 0:
                # 名单满员按最早登记淘汰；被淘汰的也要从内存记账里去掉，
                # 否则他再次活跃会被当成「今天已登记」直接跳过，永远回不到索引里。
                self._indexed.difference_update(known[:overflow])
                del known[:overflow]
            if not await kv_put(self.kv_store, _INDEX_KEY,
                                {"date": today, "uids": known}, "每日牌运用户索引"):
                return
        self._indexed.add(uid)

    async def users_for(self, today: str | None = None) -> list[str]:
        """今天有牌运记录的用户（只读；索引缺失或跨天时为空）。"""
        today = today or self.clock.today()
        raw, ok = await kv_get(self.kv_store, _INDEX_KEY, None, "每日牌运用户索引")
        if not ok or not isinstance(raw, dict) or raw.get("date") != today:
            return []
        uids = raw.get("uids")
        return [u for u in uids if isinstance(u, str)] if isinstance(uids, list) else []

    async def read_record(self, uid: str):
        """读某用户当前的牌运记录（只读，给可视化视图用）；坏记录当没有。"""
        if not uid:
            return None
        raw, ok = await kv_get(self.kv_store, self._key(uid), None, "每日牌运记录读取")
        if not ok or not isinstance(raw, dict):
            return None
        record = DailyRecord.from_raw(raw)
        return record if record.date else None

    async def _cleanup_legacy_spirit(self, uid: str) -> None:
        """删掉旧版独立的 sf_spirit_* 键（每个用户一次，删除成功后才记账）。

        先记账再删会让一次瞬时故障变成「永远不再尝试」；失败时下次调用再试即可。
        """
        if not uid or uid in self._legacy_cleaned:
            return
        if await kv_delete(self.kv_store, f"sf_spirit_{uid}", "旧牌灵缓存"):
            self._legacy_cleaned.add(uid)

    async def pick_cached(self, uid: str, today: str | None = None) -> tuple | None:
        """今日固定抽牌：一个用户一条记录，按日期判失效。

        today 是本次占卜的日期上下文（由 ReadingService.prepare 固定一次）；
        不传时按自身时钟取当前日期。

        返回 (formation, positions, picks)；uid 为空时返回 None，
        由调用方统一回退自由随机（确定性函数保证 KV 异常也不丢固定）。
        """
        if not uid:
            return None
        await self._cleanup_legacy_spirit(uid)
        today = today or self.clock.today()
        key = self._key(uid)
        async with _shared_kv_lock():
            raw, ok = await kv_get(self.kv_store, key, None, "每日牌运缓存")
            record = DailyRecord.from_raw(raw)
            if ok and record.date == today:
                card = _CARDS_BY_ID.get(record.card_id)
                if card is not None:
                    await self._note_index(uid, today)
                    return _daily_result(card, record.upright)
            # 缓存坏掉不丢固定：_daily_pick 是确定性纯函数，同（用户,日期）必然同牌
            card, upright = _daily_pick(uid, today)
            if ok and record.date > today:
                # 记录已被另一个请求推进到更晚的一天：本次仍按自己的日期出牌，
                # 但不能拿旧日期回写——那会抹掉新一天的牌面、解读与牌灵缓存。
                log.warning("每日牌运缓存已是更晚一天的记录，本次不回写")
            elif ok:
                picks = [{"card": card, "upright": upright}]
                sig = _reading_sign(picks)
                keep = record.same_context(today, sig, _PROMPT_V, self.persona)
                fresh = record if keep else DailyRecord()
                fresh.put_pick(card["id"], upright, sig, today, _PROMPT_V, self.persona)
                if not keep:
                    fresh.interps, fresh.spirit_line, fresh.spirit_sign = {}, "", ""
                await kv_put(self.kv_store, key, fresh.to_raw(), "每日牌运缓存")
                await self._note_index(uid, today)
            else:
                log.warning("每日牌运缓存不可用，改由确定性函数直接出牌")
        return _daily_result(card, upright)

    async def interp_cached(self, umo: str | None, uid: str, formation: str,
                            positions: list[str], picks: list[dict], clean: str,
                            persona_eff=None, budget=None, *,
                            today: str | None = None) -> Interpretation | None:
        """今日牌运的解读缓存：按主题指纹（norm_topic）分桶。

        命中要求 date + 牌组指纹 + prompt 指纹 + 人设配置一致；同一主题的并发 miss
        由 SingleFlight 去重，等待者拿同一段话。缓存命中只读不写。
        today 由调用方传入本次占卜固定的日期（不传则取当前日期），
        写回端的跨天判定也用它，整条流水线不会各算各的日期。
        """
        if not self.ai_enabled:
            # 缓存里只有 AI 产物：关闭 AI 时按未命中处理，交给本地牌义。
            return None
        topic = norm_topic(clean)
        today = today or self.clock.today()
        key = self._key(uid)
        sig = _reading_sign(picks)
        reg_key = (key, sig, topic, today, _PROMPT_V, self.persona)
        hit = None
        async with _shared_kv_lock():
            raw, ok = await kv_get(self.kv_store, key, None, "每日牌运解读缓存")
            record = DailyRecord.from_raw(raw)
            if ok and record.same_context(today, sig, _PROMPT_V, self.persona):
                hit = record.interps.get(topic)
        if hit is not None:
            cached = interpretation_from_record(hit)
            # 有效 = 有非空文本，且结构化记录至少有一段可发送的内容。
            # 只判文本会让「text 非空、sections/summary 全空」的记录通过，
            # 发送层切不出段，用户依旧收不到解读，也走不到本地牌义兜底。
            deliverable = cached.text.strip() and (
                not cached.structured
                or bool(cached.summary.strip())
                or any(seg.strip() for seg in cached.sections))
            if deliverable and self._structure_ok(cached.text, len(positions)):
                return cached
            if not deliverable:
                # 桶内容损坏或为空：KV 是持久化边界，外层上下文合法不代表内容有效。
                # 当未命中重新生成，写回时覆盖该桶。
                log.warning(f"每日牌运解读缓存内容无效（{uid}），按未命中重新生成")

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

        async with _shared_kv_lock():
            fresh_raw, fresh_ok = await kv_get(self.kv_store, key, None, "每日牌运解读缓存")
            if not fresh_ok:
                # 读故障：旧记录未知，写回会拿空壳覆盖牌面与已有分桶
                return interp
            fresh = DailyRecord.from_raw(fresh_raw)
            # 生成跨过了午夜：KV 里已经是新一天的记录，别用昨天启动的写回盖掉
            if fresh.date > today:
                return interp
            if not fresh.same_context(today, sig, _PROMPT_V, self.persona):
                # 同一天同一牌组、只是提示词/人设变了：只作废旧主题分桶。
                # 牌字段与牌灵缓存不属于解读上下文，不能让重建把它们一起抹掉。
                if fresh.date == today and fresh.sign == sig:
                    fresh.interps = {}
                    if fresh.prompt_v != _PROMPT_V:
                        # 提示词版本变了：牌灵那句是同一条记录里的旧产物，跟着作废。
                        # 否则 put_context 刷新 prompt_v 后，它会被当成新版本的缓存。
                        fresh.spirit_line, fresh.spirit_sign = "", ""
                        fresh.spirit_persona = ""
                else:
                    fresh = DailyRecord()
            parsed = interp if isinstance(interp, Interpretation) else parse_interpretation(str(interp))
            if parsed is not None:
                fresh.put_interp(topic, parsed.to_record(), sig, today, _PROMPT_V, self.persona)
            else:
                fresh.put_context(today, sig, _PROMPT_V, self.persona)
            _trim_interp_slots(fresh.interps)
            await kv_put(self.kv_store, key, fresh.to_raw(), "每日牌运解读缓存")
        return interp

    async def spirit_cached(self, umo: str | None, uid: str, picks: list, topic: str,
                            persona_eff, budget=None, *,
                            today: str | None = None) -> str:
        """牌灵的话：AI 按人设生成，当日同牌组缓存同一句；失败回退池内当日签文。

        与解读共用 `sf_daily_*` 一条记录：同一天同一牌组只有一句；读故障时不写回，
        避免在不知道旧记录的情况下把已写好的解读分桶覆盖成空壳。
        today 由调用方传入本次占卜固定的日期（不传则取当前日期）。
        """
        today = today or self.clock.today()
        if not picks:
            return ""
        card, upright = picks[0]["card"], picks[0]["upright"]
        fallback = pick_signature(card, upright, uid, today)
        if not uid or not self.ai_enabled:
            # 关闭 AI 时牌灵直接回退池内签文：历史 AI 缓存不能绕过配置开关。
            return fallback
        await self._cleanup_legacy_spirit(uid)
        key, sig = self._key(uid), _spirit_sign(picks)
        reg_key = (key, sig, today, _PROMPT_V, self.persona)

        async with _shared_kv_lock():
            raw, ok = await kv_get(self.kv_store, key, None, "牌灵的话缓存")
            record = DailyRecord.from_raw(raw)
            if (ok and record.date == today and record.prompt_v == _PROMPT_V
                    and record.spirit_sign == sig and record.spirit_persona == self.persona
                    and record.spirit_line):
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

        async with _shared_kv_lock():
            fresh_raw, fresh_ok = await kv_get(self.kv_store, key, None, "牌灵的话缓存")
            if not fresh_ok:
                # 读故障：写回会抹掉同一条记录里的牌面与解读分桶
                return line
            fresh = DailyRecord.from_raw(fresh_raw)
            if fresh.date > today:
                # 生成跨过了午夜：KV 已是新一天的记录，别用昨天启动的写回盖掉
                return line
            if (fresh.date == today and fresh.prompt_v == _PROMPT_V
                    and fresh.spirit_sign == sig and fresh.spirit_persona == self.persona
                    and fresh.spirit_line):
                return fresh.spirit_line
            if fresh.date != today:
                fresh = DailyRecord()
            elif fresh.prompt_v != _PROMPT_V:
                # 牌灵写回也会刷新 prompt_v：旧版本生成的解读分桶不跟着作废，
                # 就会在新版本号下被当成有效缓存命中（普通占卜先于今日牌运时必现）。
                fresh.interps = {}
            # 只写牌灵字段：普通占卜的牌灵写回不许碰今日固定牌的 card_id/upright
            fresh.put_spirit(line, sig, today, _PROMPT_V, self.persona)
            await kv_put(self.kv_store, key, fresh.to_raw(), "牌灵的话缓存")
        return line
