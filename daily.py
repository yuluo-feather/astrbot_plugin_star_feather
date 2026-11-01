"""今日固定牌运：运势词判定、确定性抽牌与当日缓存。

用户标识由 identity.py 统一解析、由入口传入（daily 与限流共用同一口径，不各自实现）；
设计原则：
- 同一（用户, 日期）永远同一张牌：_daily_pick 是确定性纯函数（md5 种子），
  KV 缓存异常也不丢固定（缓存只做加速与解读复用）。
- 缓存「单 key 覆盖式」按日期判失效，零清理任务。
- KV 读写全部 try-except 静默降级：任何存储故障不阻塞占卜主流程。

说人话：今天这张牌，本羽说了算——问一百遍也是它，别想改命。
"""
import asyncio
import hashlib
import random
import re
import threading
from contextlib import AbstractAsyncContextManager, nullcontext

from astrbot.api import logger

from .card_render import IMAGE_DIR_ROOT, _render_daily_card_img, _schedule_image_cleanup
from .clock import Clock
from .dailylines import pick_signature
from .kv_utils import kv_get, kv_put
from .prompts import READING_PROMPT_V, SPIRIT_PROMPT_V
from .tarot_data import TAROT_CARDS

# AI 生成中的共享登记（牌灵的话 / 每日解读共用），键含用户、牌组、主题与日期。
# 放模块级而非实例属性：热重载前后并存的实例若各持一张表，会同时 miss、各生成一份。
# 跨模块对象（重载后旧实例还活着）没有原子 KV 原语可用，仍以最后写回者为准。
# 用 threading.Lock 而非 asyncio.Lock：临界区只有同步 dict 操作，模块级 asyncio 锁
# 会绑死第一个事件循环，测试里换循环就炸。
_INFLIGHT: dict[tuple, asyncio.Future] = {}
_INFLIGHT_GUARD = threading.Lock()


def _claim_inflight(reg_key: tuple) -> tuple[asyncio.Future, bool]:
    """认领或加入一笔同键生成，返回 (future, 是否 owner)。

    只做同步操作，调用方负责把它放进自己的锁里（跨实例共享靠模块级表 + threading 锁）。
    上个事件循环留下的死 Future 一律换新的：done 的不能再 set_result，绑在别的
    loop 上的 await 会直接报跨循环错误。
    """
    loop = asyncio.get_running_loop()
    with _INFLIGHT_GUARD:
        pending = _INFLIGHT.get(reg_key)
        if pending is not None and (pending.done() or pending.get_loop() is not loop):
            pending = None
        owner = pending is None
        if owner:
            pending = loop.create_future()
            _INFLIGHT[reg_key] = pending
    assert pending is not None   # owner 分支已建好；非 owner 分支本来就有
    return pending, owner


def _release_inflight(reg_key: tuple, pending: asyncio.Future, result=None) -> None:
    """唤醒等待者并摘除登记；没结果就给 result（None / 池内兜底句）。"""
    if not pending.done():
        pending.set_result(result)
    with _INFLIGHT_GUARD:
        # 只摘自己那张：期间可能有新请求登记了同名 Future
        if _INFLIGHT.get(reg_key) is pending:
            _INFLIGHT.pop(reg_key, None)

# 今日固定牌运判定：明确运势词直接命中；泛时间词（今天/最近/每日…）只在整句不含具体
# 主题词、且是泛问或签文类问法时才算。旧版把「最近/近期/每日」当运势词，把「最近她
# 对我什么感觉」「我最近学业怎么样」这类具体问题全吞进固定牌——问什么都拿同一张牌。
DAILY_WORDS = ("运势", "运气", "牌运", "日运", "daily")
TIME_WORDS = ("今天", "今日", "每日", "最近", "近期",
              "这个月", "本月", "这两天", "这段时间")
SPECIFIC_WORDS = (
    "感情", "爱情", "恋爱", "喜欢", "复合", "分手", "桃花", "关系",
    "事业", "工作", "学业", "考试", "考研", "面试", "升职",
    "她", "我们",
)
# 【匹配是子串（`w in text`），不是语义判定】
# 单字「他」摘除：其他／他日／吉他／他乡／他人 里都含它，命中即误判成具体问题；摘后
# 5 条误命中全修。「她」「我们」构词面窄、误命中≈0，保留（「最近她怎么样」判具体是真
# 收益）。代价是「最近他怎么样」退成泛问——这是构词风险的差异，不是漏项，别把「他」
# 补回来（补回来 TestDailyJudgement 的正向语料即红）。
# TOPIC_WORDS 只喂 _norm_topic 分桶，不参与「算不算每日牌运」：并进 SPECIFIC_WORDS 会把
# 「今天财运怎么样」踢出每日固定牌、变成每问重抽。两张表有意不同，别以「看着重复」合并；
# 动 SPECIFIC_WORDS 会同时改判定与分桶两处账。
TOPIC_WORDS = SPECIFIC_WORDS + (
    "财运", "钱财", "收入", "理财", "投资", "金钱",
    "健康", "身体", "养生", "生病", "失眠", "疾病",
    "家庭", "家人", "父母", "孩子", "亲子", "婆媳",
    "人际", "人缘", "朋友", "同事", "社交", "圈子",
    "婚姻", "结婚", "姻缘", "相亲", "离婚",
    "出行", "旅行", "搬家", "房子", "买房", "租房",
    "求职", "跳槽", "离职", "辞职", "转行", "创业",
)
# 事件归因式排除：因果连词（句式类，非事件词枚举）——「运势/运气」出现在这些
# 连词之后时，它是「事件是不是运势导致」的归因宾语，不是被查询的运势值本身。
# 真·运势查询的「运势」在主题位（句首/句尾），位于因果连词之前，不命中排除。
CAUSE_WORDS = ("是因为", "是不是因为", "难道是因为", "就是因为",
               "可能因为", "或许因为是",
               # 「是不是运气问题」这类把运势当归因宾语的问法（2026-11-01）：
               # 收「连词+运势词」的整段，而不是裸「是不是」——后者会把
               # 「我今天的运势是不是会变好」（运势在前、只是追问语气）一起吞掉
               "是不是运气", "是不是运势", "是不是牌运",
               "算不算运气", "算不算运势", "算不算牌运")

# 泛时间词分支的白名单：泛问句式（最近怎么样 / 这个月如何）与签文类（每日塔罗 /
# 今天来一签）才算；事件、日程、状态描述（最近总是失眠、今天下午开会吗）走自由随机。
# 泛问句式必须落在句尾：「今天会下雨吗，如何出门」里的「如何」是普通动词用法。
# 语气词只收内置字体子集覆盖得到的——本文件的字面量会被算进字形清单。
_GENERIC_ASK_RE = re.compile(r"(?:怎么样|如何|还好吗|咋样|怎样)[\s。！？!?~…、，呢吧]*$")
_SIGN_WORDS = ("塔罗", "一签", "签文")


def _is_event_attribution(text: str) -> bool:
    """事件归因式？——「运势/运气」出现在因果连词之后，作归因宾语而非被查询的值。

    例：「被门夹了，是因为运势太差吗」→ 是（问事件归因，走自由随机）；
    「今天运势为什么这么差」→ 否（运势在句首主题位）。
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

    ① 事件归因式（最高优先）：运势词作因果宾语 → 走自由随机；
    ② 明确运势词（运势/运气/牌运/日运/daily）直接命中——主题+运势也算
       （牌固定，解读按主题分缓存）；
    ③ 泛时间词：只有泛问句式与签文类白名单问法算，事件/日程/状态描述走自由随机。
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


_CARDS_BY_ID = {c["id"]: c for c in TAROT_CARDS}


def _cached_card(raw) -> dict | None:
    """缓存里的牌 → 牌库里的权威对象：按 id 锚点回查，查不到即视为缓存不可用。

    不用 `raw in TAROT_CARDS`：那是整字典值相等，等于把「存储层的序列化/反序列化必须
    逐字段等值」写成隐式契约——字段类型只要有一次漂移（如 num 从 str 变 int）就全部
    失配，缓存静默失效。id 是牌库明面声明的唯一锚点；命中后回查牌库对象，
    顺带避免旧版本的字面量被当成当前牌义。
    """
    if not isinstance(raw, dict):
        return None
    card_id = raw.get("id")
    return _CARDS_BY_ID.get(card_id) if isinstance(card_id, str) else None


_GENERIC_TOPIC = "（今日牌运）"

# 纯泛问判据：整句只由「时间词 / 运势词 / 签文词 / 泛问句式 / 口语填充」构成才算泛问；
# 混着具体诉求的句子（「今天运势如何，我该不该表白」）按原句分桶——它和「今天运势」
# 不是同一个问题，共用一段解读就是答非所问。语气词只收字体子集覆盖得到的几个：
# 本文件字面量会进字形清单，多一个字就得重建字体；漏掉顶多多分一个桶，不会误并。
_GENERIC_ONLY_NOISE_RE = re.compile(
    "|".join(map(re.escape, TIME_WORDS + DAILY_WORDS + _SIGN_WORDS))
    + r"|怎么样|如何|咋样|怎样|还好吗|好不好|帮我|给我|麻烦|请|看看|看一下|"
      r"占卜|算一算|算一卦|算算|抽|我|你|的|了|呢|吗|吧|[\s\W_]+",
    re.IGNORECASE,
)

# 解读分桶上限：当天换主题不回收、跨天才随 pick_cached 作废，桶数会线性增长。
# 超限丢「最早插入」的那桶（FIFO）：dict 顺序在 KV 的 JSON 往返后原样保留，淘汰就是
# 删首个 key；LRU 要多维护访问计数，而「当天问过的事基本不回头」不值这份复杂度。
# 注意 dict 重写已有 key 不改变顺序，所以严格是「最早插入」。真要压成本该动
# limit.daily_count（限流），不是把缓存做大。
DAILY_INTERP_MAX = 20


def _trim_interp_slots(slots: dict) -> None:
    """把解读分桶裁到 DAILY_INTERP_MAX：超出的按「最早插入」逐个丢（原地改）。

    只做结构维护，不做业务判定，所以不返回值、也不吞异常——调用点已判过 slots 是 dict。
    """
    while len(slots) > DAILY_INTERP_MAX:
        slots.pop(next(iter(slots)), None)


def _norm_topic(clean: str) -> str:
    """解读缓存的主题指纹：具体问题按清洗后原文；纯泛问统一为当日牌运。

    泛问措辞（「看看今天的运势」「每日一签」）当天共享同一段解读，防止反复问刷新版本；
    带具体诉求的一律按原句分桶（「今天财运怎么样」与「今天财运如何」是两个桶）。

    泛问的判据是「去掉时间/运势/签文/泛问句式/口语词后一个字都不剩」——不能简单用
    「不含 TOPIC_WORDS 即泛问」，「今天运势如何，我该不该表白」会被塞进通用桶、
    拿到当天早上的泛泛解读。TOPIC_WORDS 仍作快速通道：带主题词的问法绝不进通用桶。
    """
    raw = (clean or "").strip().lower()
    if not raw or any(w in raw for w in TOPIC_WORDS):
        return raw or _GENERIC_TOPIC
    return _GENERIC_TOPIC if not _GENERIC_ONLY_NOISE_RE.sub("", raw) else raw


def _render_slot(fortune) -> AbstractAsyncContextManager[None]:
    """渲染并发位：与核心 _render_image 共用同一把渲染锁（防多人同时占卜时内存暴涨）。

    海报与拼图同是 Pillow 全尺寸合成，绕开锁就是把最重的一条路径漏在锁外。运行时取
    而非构造期装配：桩对象没有 tarot / 没有锁时退化成不限并发，而不是崩掉渲染路径。
    返回异步上下文管理器，信号量与 nullcontext 都吃得住 async with。

    判「有没有锁」用 is not None，不用 or 短路：锁若被包成 falsy 对象，or 会静默换成
    nullcontext()，最重的渲染路径漏在锁外且日志不留痕。红线：test_render_slot_keeps_a_falsy_lock。
    """
    lock = getattr(getattr(fortune, "tarot", None), "_render_lock", None)
    return lock if lock is not None else nullcontext()


async def _ai_output(awaitable, what: str, who: str = "") -> str | None:
    """装饰性 AI 产出的统一故障边界：异常 → None，走各自的降级路。

    解释器层已吞掉 provider 级失败，能漏到这里的都是解释器自身的 bug（prompt 拼接、
    人设模板、数据形态漂移）。一处装饰性产出不该陪葬整卦：「牌灵的话」坏了退池内句、
    解读坏了退本地牌义，用户照样拿到一整卦。who 是降级日志的 uid。
    红线：tests/test_fault_injection.py::TestAiFaultBoundary
    """
    try:
        return await awaitable
    except Exception as e:
        logger.warning(f"{what}生成异常（{who or '无标识'}），走降级: {e}")
        return None


class DailyFortune:
    """今日固定牌运的缓存与判定：给入口层提供「当天固定牌 + 当天解读」。

    独立持有 kv_store 与 tarot（核心）：kv_store 须提供 get_kv_data/put_kv_data，
    应传插件实例自身（Star 基类混入 PluginKVStoreMixin）——AstrBot 的 Context
    没有这两个方法，传错会导致缓存全程静默失效。KV 异常时由确定性函数兜底；
    入口层（main.py）仅通过本类访问，不直接接触 KV 细节。
    """

    # 类级兜底（与 __init__ 同口径：本地时区 + 图片根目录）：只有 __new__ 直调才会
    # 用到（单测、诊断脚本），真实装配一律被 __init__ 的实例属性覆盖。
    # 为什么要兜而不是让它抛：渲染路径整段裹 try，AttributeError 会被当成「渲染失败」
    # 静默吞掉，测试里表现为「明明渲染桩成功了却返回 None」——今天这仨红就是这个形状。
    # 生产口径由 test_clock.py::TestClockWiring 钉住，不靠这两个默认值撑。
    clock = Clock()
    image_dir = IMAGE_DIR_ROOT

    def __init__(self, kv_store, tarot):
        # kv_store 传插件自身（见类 docstring）、tarot 是 StarTarot 实例
        self.kv_store = kv_store
        self.tarot = tarot
        # 日期边界的唯一来源（clock.Clock）：取 tarot 上那份（同一实例内与框架时区口径
        # 一致）；桩对象没有就自建一份跟随本地时区——不因诊断/装配形态让日期键失联
        self.clock = getattr(tarot, "clock", None) or Clock()
        # 图片目录同样取自核心那份（①实例隔离：海报卡别落到共享父目录去）；
        # 桩对象没有这个属性才退回根目录（诊断/装配形态不该让渲染无处可放）。
        # 用 isinstance 而不是 `or`：核心的图片退路彻底失败时会给出空串，那是
        # 「降级为无图」的信号——`or` 会把它悄悄换成共享根目录，破坏①的每实例一份。
        _img_dir = getattr(tarot, "image_dir", None)
        self.image_dir = _img_dir if isinstance(_img_dir, str) else IMAGE_DIR_ROOT
        # 缓存读-判-写串行化（gating 同款防线）：并发占卜时两条协程各自读旧值再写回，
        # 后写的会把先写的分桶整个覆盖掉（实测：并发问两个主题，KV 里只剩一个）
        self._kv_lock = asyncio.Lock()
        # 同一（用户, 牌组, 今天）正在生成的牌灵的话登记在模块级 _INFLIGHT：
        # 并发 miss 只让一个人去问 AI，其余等它的结果——否则两句话各自成文，
        # 破坏「当日同牌组同一句」。跨实例共享的理由见模块级注释。

    async def render_daily_card(self, topics: list[str], picks: list[dict], uid: str) -> str | None:
        """今日牌运卡海报编排：签文（确定性函数）+ 日期文本 + 线程池渲染 + 登记清理。

        渲染失败一律返回 None——调用方回退普通牌面图，不拦占卜主流程；
        清理走 card_render 的统一保险丝（曾单列 300 秒「别转个身就没了」，
        其实内容在发送那一刻就被平台取走了，本地文件活多久与用户无关——见
        IMAGE_TTL_SECONDS）。topics 保留接口（标题固定「星羽塔罗·今日牌运」）。
        """
        try:
            card = picks[0]["card"]
            upright = bool(picks[0]["upright"])
            date_text = self.clock.date_text()
            signature = pick_signature(card, upright, uid, self.clock.today())
            async with _render_slot(self):
                # 显式传本实例的目录（①实例隔离）：海报卡与普通牌面图同住一处，
                # 都由本实例自己的清理路径负责
                img = await asyncio.to_thread(_render_daily_card_img, card, upright, signature,
                                              date_text, self.image_dir)
            if not img or not isinstance(img, str) or not img.strip():
                return None
            _schedule_image_cleanup(img)
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
        key, today = f"sf_daily_{uid}", self.clock.today()
        # 读-判-写原子化（gating 同款防线）：并发时两条协程各自读旧值再写回，
        # 后写的会把先写的字段整个覆盖掉
        async with self._kv_lock:
            data, ok = await kv_get(self.kv_store, key, None, "每日牌运缓存")
            if ok and isinstance(data, dict) and data.get("date") == today:
                card, upright = _cached_card(data.get("card")), data.get("upright")
                if card is not None and isinstance(upright, bool):
                    return _daily_result(card, upright)
            # 缓存坏掉不丢固定：_daily_pick 是确定性纯函数，同（用户,日期）必然同牌
            card, upright = _daily_pick(uid, today)
            if ok:
                # 只有正常读到（ok=True）才合并写回：读故障时不写（写回了也未必可靠、
                # 且旧实现同样跳过写回）；无记录时 data=None，合并从空壳起步即可
                # 合并写而非覆盖：保留可能已存在的 interps——解读先写入时条目里没有
                # card/upright（interp_cached 只写 date+interps），由本函数合并补齐，
                # 抽牌重生成也不丢弃已写好的解读分桶
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
                            persona_eff=None, budget=None) -> str | None:
        """今日牌运的解读缓存：按主题指纹（_norm_topic）分桶缓存。

        泛问当天固定同一段解读（防反复问刷版本）；带主题的各桶独立——同主题复用、
        换主题现场生成、换回来不丢。旧版单槽无主题区分，「问什么都一个答案」。
        桶数硬上界是 DAILY_INTERP_MAX（FIFO），跨天由 pick_cached 整体回收。

        命中要求 date + 牌组指纹 + prompt 指纹 + 人设配置一致；同一主题的并发 miss
        由模块级 in-flight 登记去重，等待者拿同一段话（与牌灵的话同一套口径）。
        budget 原样透传：命中路径不碰 AI，未命中现场生成才吃这笔预算。
        """
        topic = _norm_topic(clean)
        key, today = f"sf_daily_{uid}", self.clock.today()
        # 牌指纹：同一天换牌（升级、修 KV、牌库顺序变化）后，旧解读不再算命中。
        # 用 id + 正逆（稳定标识），不用牌名——改名不该让缓存整体失效。
        sig = "|".join(f"{p['card']['id']}:{1 if p['upright'] else 0}" for p in picks)
        # 面板切人设（ai.persona gentle → tsundere）也要让当天旧解读失效：
        # v 只覆盖「模板文本改版」，配置选择属于另一维，单独进键与记录。
        # random 按原值参与——同一天仍是先到的那句，不因每次掷骰换口吻。
        persona_cfg = str(getattr(self.tarot, "ai_persona", "") or "")
        # 同一天同一主题的并发生成也只允许一个人去问 AI：与牌灵的话同一套登记表。
        # 登记键含 prompt 指纹与日期——升级换了提示词/人设时，旧生成不能喂给新请求。
        reg_key = (key, sig, topic, today, READING_PROMPT_V, persona_cfg)
        # 命中判定原子化；写回另起一次锁（AI 调用不占锁，见下）
        hit = None
        async with self._kv_lock:
            data, ok = await kv_get(self.kv_store, key, None, "每日牌运解读缓存")
            # prompt 指纹进命中条件：升级换了提示词/人设，牌组没变也要重生成，
            # 否则当天会继续吐旧口吻的解读（牌灵那句早就有这层保护）。
            if ok and isinstance(data, dict) and data.get("date") == today \
                    and data.get("v") == READING_PROMPT_V \
                    and data.get("persona") == persona_cfg:
                slots = data.get("interps")
                if isinstance(slots, dict):
                    # 牌变了就不是同一签：旧解读必须重生成（2026-10-17 复核）
                    if data.get("sign") == sig:
                        hit = slots.get(topic)
            pending, owner = (None, False) if hit else _claim_inflight(reg_key)
        if hit:
            # 命中路径只读不写：无 CAS 的 KV 上，任何「读旧值→裁→写回」都可能覆盖并发生成
            # 的新主题（热重载前后两代还各持一把实例锁）。上限交给真正写入的那条路径。
            return hit
        assert pending is not None
        if not owner:
            # 同一主题已经有人在生成：等它的结果，别再问一次 AI（否则同一主题
            # 并发会各生成一段、互相覆盖）。取消只退出等待，不影响共享 Future。
            try:
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                raise
            except Exception:
                return None
        try:
            interp = await _ai_output(
                self.tarot._ai_interpret(
                    event, formation, positions, picks, clean or "（今日牌运）",
                    persona_eff=persona_eff, budget=budget),
                "AI 解读", uid)
            if ok:
                async with self._kv_lock:
                    # 重读再合并：本主题生成期间别的主题可能已写回，拿旧 data 写回会把它覆盖掉
                    fresh, _ = await kv_get(self.kv_store, key, None, "每日牌运解读缓存")
                    # 类型校验链，勿退回 `dict(data or {})`：`or` 只兜 falsy，truthy 非 dict
                    # （外部工具或旧版写坏）会在 dict(...) 抛错，写回整段跳过 → 坏值永不
                    # 被覆盖，该用户缓存永久失效。三选一没有降级差异，但把三条路显式摆着。
                    # 红线：tests/test_core.py::TestDailyPoisonedCacheValue
                    if isinstance(fresh, dict):
                        data = dict(fresh)
                    elif isinstance(data, dict):
                        data = dict(data)
                    else:
                        data = {}
                    # 读故障（ok=False）时整段跳过写回：拿旧 data 覆盖会把有效内容写成空壳。
                    # 跨天即作废：不自己判就会把昨天的分桶盖上今天的日期复活；
                    # 同一天、同一组牌、同一版 prompt、同一人设才沿用旧分桶，任一变了就清空。
                    same_day = (data.get("date") == today and data.get("sign") == sig
                                and data.get("v") == READING_PROMPT_V
                                and data.get("persona") == persona_cfg)
                    slots = (dict(data.get("interps") or {})
                             if same_day and isinstance(data.get("interps"), dict) else {})
                    data["date"], data["interps"], data["sign"] = today, slots, sig
                    data["v"] = READING_PROMPT_V
                    data["persona"] = persona_cfg
                    # 失败不落 falsy：interp 为 None / 空串时只刷新结构，不写分桶——立住
                    # 「interps 有 key 必为有效解读」这条不变量（红线 TestDailyNullInterp）。
                    if interp:
                        data["interps"][topic] = interp
                    # 写回前一律裁剪：AI 失败时也裁，旧超大字典不会原样写回。
                    _trim_interp_slots(data["interps"])
                    await kv_put(self.kv_store, key, data, "每日牌运解读缓存")
            pending.set_result(interp)   # 唤醒并发等待者：同一段解读
            return interp
        finally:
            _release_inflight(reg_key, pending, None)

    async def spirit_cached(self, event, uid: str, picks: list, clean: str,
                            persona_eff, budget=None) -> str:
        """牌灵的话：AI 按人设生成（贴合本次牌面+主题），当日同牌组缓存同一句；
        AI 失败回退池内当日签文（不写缓存，下次恢复后重新生成）。

        所有牌阵共用：缓存键 sf_spirit_{uid}，单 key 覆盖，命中条件=当日+同牌组指纹
        （顺序+正逆）。与海报分工：海报印池内固定句，聊天这句是人设化的开口。

        键里刻意不含 persona：同牌组即同一签，「本签全程不变」本就要同签同一句；
        唯一残留是当天改了 ai.persona 又抽到同一牌组时沿用旧口吻，低频、隔天自然消解。
        SPIRIT_PROMPT_V 把所有人设模板算进指纹，改模板即全缓存失效——有意的保守设计。

        与另两个缓存的差别：本函数是纯覆盖写（只写 v/date/sign/line），读故障不影响
        正确性，所以照样写回——门住的话，故障恢复前的重复查询会重新生成、措辞漂移，
        「当日同牌组同一句」就破了。
        红线：tests/test_core.py::TestSpiritCache::test_spirit_writes_back_even_on_read_failure
        """
        today = self.clock.today()
        if not picks:
            return ""
        card, upright = picks[0]["card"], picks[0]["upright"]
        fallback = pick_signature(card, upright, uid, today)
        if not uid:
            return fallback  # 无标识：连缓存都无从谈起，直接池内兜底
        key = f"sf_spirit_{uid}"
        sig = "|".join(f"{p['card']['cn']}:{1 if p['upright'] else 0}" for p in picks)
        # 日期进登记键：跨午夜仍在生成的那一句属于昨天，新一天的请求不能加入它，
        # 否则会把昨天的文案当成今天的（缓存命中本来就按 date 判，登记也得同口径）。
        reg_key = (key, sig, today)

        # 命中判定与 in-flight 登记都放在锁内：并发同签只允许一个人去问 AI。
        # 生成本身在锁外（AI 可能跑几十秒），写回再进锁。
        async with self._kv_lock:
            data, ok = await kv_get(self.kv_store, key, None, "牌灵的话缓存")
            if ok and isinstance(data, dict) and data.get("v") == SPIRIT_PROMPT_V \
                    and data.get("date") == today and data.get("sign") == sig \
                    and data.get("line"):
                return data["line"]
            pending, owner = _claim_inflight(reg_key)
        assert pending is not None   # owner 分支已建好；非 owner 分支本来就有

        if not owner:
            # 别人正在生成：等它的那一句；它失败/被取消就跟着池内兜底，
            # 绝不在这里再问一次 AI（并发风暴正是要避免的东西）
            try:
                # shield：等待者被取消时不能把共享 Future 一起取消——否则所有者
                # 稍后 set_result 会抛 InvalidStateError，所有等待者一起陪葬。
                return await asyncio.shield(pending)
            except asyncio.CancelledError:
                raise
            except Exception:
                return fallback

        # topic 传 _norm_topic 的指纹而非用户原话：本缓存的命中键不含 topic，同牌组当天
        # 本就该是同一句，传原话只会让这个定型值随用户措辞漂移。
        try:
            line = await _ai_output(
                self.tarot.interpreter.spirit_line(
                    event, [(p["card"], p["upright"]) for p in picks],
                    _norm_topic(clean), persona_eff, budget=budget),
                "牌灵的话", uid)
            if not line:
                return fallback  # AI 失败：池内当日句；不写缓存，下次再试 AI
            async with self._kv_lock:
                # 写回前再读一眼：另一个实例可能刚写好同一天同一句，有就采用它——
                # 同一签两句话正是这条缓存要防的事。
                fresh, fresh_ok = await kv_get(self.kv_store, key, None, "牌灵的话缓存")
                if fresh_ok and isinstance(fresh, dict) and fresh.get("v") == SPIRIT_PROMPT_V \
                        and fresh.get("date") == today and fresh.get("sign") == sig \
                        and fresh.get("line"):
                    line = fresh["line"]
                else:
                    await kv_put(self.kv_store, key, {"v": SPIRIT_PROMPT_V, "date": today,
                                                      "sign": sig, "line": line},
                                 "牌灵的话缓存")
            pending.set_result(line)   # 唤醒并发等待者：同一句
            return line
        finally:
            # 成功、失败、取消都要唤醒等待者并摘除登记；没结果就给池内兜底
            _release_inflight(reg_key, pending, fallback)
