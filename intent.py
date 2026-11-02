"""意图判定：一句原话 → 一个结构化的 ReadingRequest。

原先「这句话是什么意图」散在 daily / spreads / main 三处：每日牌运判定、
选阵、主题分桶、帮助判定、问题清洗各有一套词表，口径迟早会漂（他/喜欢/转行
的误判就是这么来的）。现在全部收在这里：

- 词表只有一份：KEYWORD_MAP 的情感 / 事业 / 时间线三组是选阵依据，
  每日牌运的 SPECIFIC_WORDS 直接由其中两组派生，不再手抄第二份；
- 判定只有一个入口：build_request(kind, text, ...) 返回 ReadingRequest；
- 三个入口只负责构造它，_pick_reading 不再接受 force_daily / fixed_formation 这类布尔参数。

判定顺序（对用户可见的行为）：
1. 帮助请求 → kind=help
2. 今日牌运（配置允许时）→ kind=daily，固定牌、按主题分桶缓存
3. 其余 → kind=spread，规则选阵；规则没有信号时，自然语言入口可采纳模型给的
   formation（枚举外一律忽略，回落默认阵）。命令入口不采纳模型输入，保持可预测。
"""
import re
from dataclasses import dataclass

from .spreads import FORMATIONS

KIND_DAILY = "daily"
KIND_SINGLE = "single"
KIND_SPREAD = "spread"
KIND_HELP = "help"

DEFAULT_FORMATION = "羽时三刻"
_GENERIC_TOPIC = "（今日牌运）"


@dataclass(frozen=True)
class ReadingRequest:
    """一次占卜请求的结构化描述：入口层只消费它，不再各自做判定。"""

    kind: str          # daily / single / spread / help
    formation: str     # 牌阵名（help 时为空串）
    fixed: bool        # 是否走今日固定牌运缓存
    question: str      # 清洗后的问题主体（送 AI / 做主题指纹）
    topic: str         # 解读缓存的主题分桶键


# ---- 语义关键词 → 牌阵（权重累计）----
# 注意：运势类词（运势/运气/牌运/日运/每日/近期/最近）不在此列——
# 它们由 is_daily_request 先行判定（今日固定牌运），只有 daily_fixed 关闭时才落到本层/兜底。
# 「喜欢」不收（2026-11-01）：构词面分不开兴趣爱好与爱慕（「我喜欢做设计，要不要转行」
# 会被判成恋爱阵）。感情问法几乎都带主体（他/她/我们）或情感词，由 RELATION_WORDS 接住。
# 转行/跳槽/离职/辞职/求职归事业组（2026-11-02）。
KEYWORD_MAP = [
    (("情感", "爱情", "恋爱", "感情", "分手", "复合"), "恋羽十字"),
    (("事业", "工作", "面试", "学业", "考试", "考研", "升职",
      "转行", "跳槽", "离职", "辞职", "求职"), "羽镜"),
    (("过去", "未来", "时间线"), "羽时三刻"),
]

# 关系语义词（选阵推断层与「时间线词冲突」判定共用）：
# 感情类词**直接引用 KEYWORD_MAP 感情组**，不再抄一份字面量。抄过的那份曾漏「情感」：
# 时间线词 ≥2 且不含关系主体时（「情感过去未来」），情感组得分落后 → best=羽时三刻 →
# 这里升级判定失效，关系问题被当成纯时间线问题。
# 单字「他/她」不进表：子串匹配会把「其他／吉他／他乡」判成关系问题（2026-11-01）。
# 关系主体改成「摘掉 _NON_RELATION_WORDS 后句子里还剩不剩他/她」，比枚举短语更全。
# 「喜欢」仍不进通用关键词，但补了带宾语的短语与爱慕向的词：只写「喜欢」会把爱好也算进去
# （「我喜欢做设计」），而「喜欢她」「想表白」这类问法确实需要恋羽十字（2026-11-01 复核）。
RELATION_WORDS = tuple(KEYWORD_MAP[0][0]) + (
    "我们", "喜欢我吗", "还爱",
    "喜欢你", "喜欢我", "喜欢他", "喜欢她", "喜欢一个人", "暗恋", "表白",
)

# 「他」的构词面：这些词里的「他」不是关系主体，判关系前先整词摘掉。
# 摘掉后剩下的「他」才算句子有对象——「他会来吗」「我和他的过去」照旧命中。
_NON_RELATION_WORDS = ("其他", "其它", "吉他", "他人", "他乡", "他日", "他处", "他国", "利他")

# 阵名别名（新名 + 经典名）：用户显式指定时直接采用（顺序即优先级）
FORMATION_ALIASES = {
    "羽签": "羽签", "单张问询": "羽签", "单抽": "羽签",
    "羽时三刻": "羽时三刻", "时间之流": "羽时三刻", "三张时间线": "羽时三刻",
    "羽镜": "羽镜", "圣三角": "羽镜",
    "恋羽十字": "恋羽十字", "恋人十字": "恋羽十字", "恋羽": "恋羽十字",
}

# 阵名剥离边界：前后为空白或中英文标点才算「独立词」，避免误伤动词用法（如「单抽一张」）
_ALIAS_BOUNDARY = "，。、！？：；,.!?;: \t\n"
_ALIAS_PATTERNS = tuple((re.compile(rf"(?<![^{_ALIAS_BOUNDARY}]){re.escape(a)}(?![^{_ALIAS_BOUNDARY}])"), a)
                       for a in FORMATION_ALIASES)

# 祈使词剥离：注意顺序——长词在前（「抽一张牌」先于「抽一张」），
# 匹配一次只剥一个词，循环处理。这顺序不能乱——乱了会剥成半截词，别问本羽怎么知道的。
REQUEST_WORDS = re.compile(
    r"^(?:帮我|给我|麻烦|请|求你|来一卦|来|算算|算一算|算一卦|抽一签|抽一支签|"
    r"抽一张牌|抽一张|抽个|抽牌|抽签|看看|测测|占卜一下|占卜)\s*"
)


# ---- 每日牌运判定 ----
# 明确运势词直接命中；泛时间词（今天/最近/每日…）只在整句不含具体主题词、且是泛问或
# 签文类问法时才算。旧版把「最近/近期/每日」当运势词，把「最近她对我什么感觉」「我最近
# 学业怎么样」这类具体问题全吞进固定牌——问什么都拿同一张牌。
DAILY_WORDS = ("运势", "运气", "牌运", "日运", "daily")
TIME_WORDS = ("今天", "今日", "每日", "最近", "近期",
              "这个月", "本月", "这两天", "这段时间")
# 具体主题词：由选阵词表的两组派生，再加几个只在「算不算泛问」里用到的词。
# 派生而不是手抄：选阵新增一个关键词（比如转行），每日牌运判定自动跟上，
# 不会出现「KEYWORD_MAP 认识、SPECIFIC_WORDS 不认识」的两笔账（2026-11-02）。
SPECIFIC_WORDS = tuple(dict.fromkeys(
    KEYWORD_MAP[0][0] + KEYWORD_MAP[1][0]
    + ("喜欢", "桃花", "关系", "她", "我们")
))
# 【匹配是子串（`w in text`），不是语义判定】
# 单字「他」摘除：其他／他日／吉他／他乡／他人 里都含它，命中即误判成具体问题；摘后
# 5 条误命中全修。「她」「我们」构词面窄、误命中≈0，保留（「最近她怎么样」判具体是真
# 收益）。代价是「最近他怎么样」退成泛问——这是构词风险的差异，不是漏项，别把「他」
# 补回来（补回来 TestDailyJudgement 的正向语料即红）。
# TOPIC_WORDS 只喂 norm_topic 分桶，不参与「算不算每日牌运」：并进 SPECIFIC_WORDS 会把
# 「今天财运怎么样」踢出每日固定牌、变成每问重抽。两张表有意不同，别以「看着重复」合并；
# 动 SPECIFIC_WORDS 会同时改判定与分桶两处账。
TOPIC_WORDS = tuple(dict.fromkeys(SPECIFIC_WORDS + (
    "财运", "钱财", "收入", "理财", "投资", "金钱",
    "健康", "身体", "养生", "生病", "失眠", "疾病",
    "家庭", "家人", "父母", "孩子", "亲子", "婆媳",
    "人际", "人缘", "朋友", "同事", "社交", "圈子",
    "婚姻", "结婚", "姻缘", "相亲", "离婚",
    "出行", "旅行", "搬家", "房子", "买房", "租房",
    "创业",
)))
# 事件归因式排除：因果连词（句式类，非事件词枚举）——「运势/运气」出现在这些
# 连词之后时，它是「事件是不是运势导致」的归因宾语，不是被查询的运势值本身。
# 真·运势查询的「运势」在主题位（句首/句尾），位于因果连词之前，不命中排除。
CAUSE_WORDS = ("是因为", "是不是因为", "难道是因为", "就是因为",
               "可能因为", "或许因为是",
               # 「是不是运气问题」这类把运势当归因宾语的问法（2026-11-01）：
               # 只收「连词+运势词+问题」的整段——裸「是不是运气」会把
               # 「今天是不是运气好」（真·运势查询）也赶去自由随机（review 复核）
               "是不是运气问题", "是不是运势问题", "是不是牌运问题",
               "算不算运气问题", "算不算运势问题", "算不算牌运问题")

# 泛时间词分支的白名单：泛问句式（最近怎么样 / 这个月如何）与签文类（每日塔罗 /
# 今天来一签）才算；事件、日程、状态描述（最近总是失眠、今天下午开会吗）走自由随机。
# 泛问句式必须落在句尾：「今天会下雨吗，如何出门」里的「如何」是普通动词用法。
# 句尾语气词只收 啊/呀/嘛 这几个：本文件不在 tools/build_font_subset.py 的扫描范围内，
# 增删语气词不必重建字体子集。
_GENERIC_ASK_RE = re.compile(
    r"(?:怎么样|如何|还好吗|咋样|怎样)[\s。！？!?~…、，呢吧啊呀嘛]*$")
_SIGN_WORDS = ("塔罗", "一签", "签文")


def _is_event_attribution(text: str) -> bool:
    """事件归因式？——「运势/运气」出现在因果连词之后，作归因宾语而非被查询的值。

    Args:
        text: 已小写的用户原话。

    Returns:
        是事件归因式则 True（走自由随机）。
    """
    for cause in CAUSE_WORDS:
        i = text.find(cause)
        if i < 0:
            continue
        if any(w in text[i:] for w in DAILY_WORDS):
            return True
    return False


def is_daily_request(text: str) -> bool:
    """判断是否为「今日牌运」类请求，按类别判定（自上而下）：

    ① 事件归因式（最高优先）：运势词作因果宾语 → 走自由随机；
    ② 明确运势词（运势/运气/牌运/日运/daily）直接命中——主题+运势也算
       （牌固定，解读按主题分缓存）；
    ③ 泛时间词：只有泛问句式与签文类白名单问法算，事件/日程/状态描述走自由随机。

    Args:
        text: 用户原话。

    Returns:
        应当走今日固定牌运则 True。
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


# 纯泛问判据：整句只由「时间词 / 运势词 / 签文词 / 泛问句式 / 口语填充」构成才算泛问；
# 混着具体诉求的句子（「今天运势如何，我该不该表白」）按原句分桶——它和「今天运势」
# 不是同一个问题，共用一段解读就是答非所问。语气词只收字体子集覆盖得到的几个：
# 本文件字面量会进字形清单，多一个字就得重建字体；漏掉顶多多分一个桶，不会误并。
_GENERIC_ONLY_NOISE_RE = re.compile(
    "|".join(map(re.escape, TIME_WORDS + DAILY_WORDS + _SIGN_WORDS))
    + r"|怎么样|如何|咋样|怎样|还好吗|好不好|帮我|给我|麻烦|请|看看|看一下|"
      r"来一签|有没有|有什么|占卜|算一算|算一卦|算算|抽|我|你|的|了|呢|吗|吧|[\s\W_]+",
    re.IGNORECASE,
)


def norm_topic(clean: str) -> str:
    """解读缓存的主题指纹：具体问题按清洗后原文；纯泛问统一为当日牌运。

    泛问措辞（「看看今天的运势」「每日一签」）当天共享同一段解读，防止反复问刷新版本；
    带具体诉求的一律按原句分桶（「今天财运怎么样」与「今天财运如何」是两个桶）。

    泛问的判据是「去掉时间/运势/签文/泛问句式/口语词后一个字都不剩」——不能简单用
    「不含 TOPIC_WORDS 即泛问」，「今天运势如何，我该不该表白」会被塞进通用桶、
    拿到当天早上的泛泛解读。TOPIC_WORDS 仍作快速通道：带主题词的问法绝不进通用桶。

    Args:
        clean: 已清洗的问题主体。

    Returns:
        主题分桶键；纯泛问返回 _GENERIC_TOPIC。
    """
    raw = (clean or "").strip().lower()
    if not raw or any(w in raw for w in TOPIC_WORDS):
        return raw or _GENERIC_TOPIC
    return _GENERIC_TOPIC if not _GENERIC_ONLY_NOISE_RE.sub("", raw) else raw


# ---- 帮助判定 ----
# 帮助请求判定：整句剥掉祈使词后只剩「帮助 / help / 说明」才算（"帮助" in text 的
# 子串判定会把「帮助我做出决定」误当帮助页）。另有一条宽松分支：整段只有一句话、
# 且带「怎么用 / 怎么玩 / 用法」也算——这类问法引导词太多，按意图词判定更稳。
# 正则里冗余的「说说 / 讲讲」分支已删：前缀重叠会让回溯指数增长（py/redos 真阳性）；
# 不用原子组是因为它会掐掉前缀的贪婪回退，把「说明」判成非帮助——那是改行为，不是修缺陷。
# 防回归：语料与计时红线在 tests/test_judgement_corpus.py。
_HELP_REQUEST_RE = re.compile(
    r"^(?:请|帮我|给我|麻烦|来|看看|看下|查一下|查查|使用|查看|瞅瞅|"
    r"说|讲|告诉|教|说下|讲下|说一下|讲一下)*\s*"
    r"(?:帮助|help|帮助说明|使用说明|说明)(?:\s*(?:一下|下))?\s*[？?！!。.]*$",
    re.IGNORECASE,
)
_HELP_USAGE_RE = re.compile(
    r"^[^？?！!。]*(?:怎么用|怎么玩|怎么使用|怎么操作|用法)[？?！!。]*$"
)


def _is_single_line(text: str) -> bool:
    """宽松分支的前置：整段只能有一行。

    多行输入通常是粘贴过来的问题（「我最近失眠 + 换行 + 怎么用」），判成帮助页会把
    问题整个吞掉；漏判只是走正常占卜、用户再打一句「怎么用」就进帮助了。两个方向的
    错误严重度不对称，所以这里偏保守。用 splitlines() 认十种换行，比枚举 \\n 可靠。

    Args:
        text: 已 strip 的用户原话。

    Returns:
        整段只有一行则 True。
    """
    return len(text.splitlines()) == 1


def is_help_request(text: str) -> bool:
    """帮助判定唯一入口：两个分支任一命中即算，三个入口与语料测试共用。

    别在别处复刻这段判定：复刻的那份会在调用方收紧（例如多行闸门）时失联——
    测试继续绿，生产行为却已经变了。

    两处 fullmatch 各有职责，不是同一件事的两遍：_HELP_USAGE_RE 的前置字符类允许
    换行、尾部又没有 \\s*，只有整段匹配才拦得住尾换行；这份等价靠上面的 strip() 撑着，
    拿掉 strip 它就会在带尾换行的输入上退化成 match。别以「看着重复」为由动它们。

    Args:
        text: 用户原话。

    Returns:
        是帮助请求则 True。
    """
    t = (text or "").strip()
    return bool(_HELP_REQUEST_RE.fullmatch(t)
                or (_is_single_line(t) and _HELP_USAGE_RE.fullmatch(t)))


# ---- 选阵与问题清洗 ----
def _decide_formation(text: str) -> tuple[str, bool]:
    """规则选阵，同时报告「规则有没有信号」。

    返回 (阵名, 是否有信号)。没有信号时返回默认阵——自然语言入口可以拿模型给的
    formation 覆盖它；命令入口保持默认，保证可预测。
    """
    text = (text or "").strip()
    for alias, name in FORMATION_ALIASES.items():
        if alias in text:
            return name, True
    # 关系主体：先摘掉「其他／吉他／他乡」这类构词，再看还剩不剩他/她/关系词
    relation_text = text
    for word in _NON_RELATION_WORDS:
        relation_text = relation_text.replace(word, "")
    has_relation = ("他" in relation_text or "她" in relation_text
                    or any(k in relation_text for k in RELATION_WORDS))
    best_name, best_score = None, 0
    for keywords, name in KEYWORD_MAP:
        score = sum(1 for k in keywords if k in text)
        if score > best_score:  # 严格大于：平局保持 KEYWORD_MAP 顺序优先级
            best_name, best_score = name, score
    if best_name:
        # 时间线词 + 关系语义 = 关系预测（「我们的未来」「她未来会爱我吗」），
        # 不是纯时间线问题——时间线阵只留给无关系主体的问法（「我的未来」）。
        if best_name == "羽时三刻" and has_relation:
            return "恋羽十字", True
        return best_name, True
    if has_relation:
        return "恋羽十字", True
    return DEFAULT_FORMATION, False


def select_formation(text: str) -> str:
    """四段决策：显式指定阵名 > 关键词权重累计 > 内容推断 > 兜底。

    Args:
        text: 用户原话。

    Returns:
        牌阵名。
    """
    return _decide_formation(text)[0]


def strip_alias(text: str) -> str:
    """剥离「作为独立词出现」的阵名（前后为空白/标点/边界），避免干扰 AI 语义；
    句中动词用法如「单抽一张牌」不受影响（选阵层保留宽松匹配，此处才收紧）。

    Args:
        text: 用户原话。

    Returns:
        去掉阵名后的文本。
    """
    text = text or ""
    for pat, _ in _ALIAS_PATTERNS:
        text = pat.sub("", text)
    return re.sub(r"[ 	]{2,}", " ", text).strip(" 	，。、！？：；,.!?;:")


def clean_question(text: str) -> str:
    """问题清洗：剥掉句首祈使词与阵名，保留问题主体。

    「帮我算一卦我和她的感情」→「我和她的感情」；剥完为空说明用户没给具体
    问题（比如只说了「帮我看一卦」），由调用方用默认兜底语句。

    Args:
        text: 用户原话。

    Returns:
        清洗后的问题主体（可能为空串）。
    """
    text = strip_alias(text or "")
    while True:
        new = REQUEST_WORDS.sub("", text.strip())
        if new == text:
            break
        text = new
    return text.strip("，,。.！!？? ")


# ---- 结构化请求 ----
def _make(kind: str, formation: str, fixed: bool, text: str) -> ReadingRequest:
    """把已选定的 kind / 阵名 / 固定位与清洗结果打成一个 ReadingRequest。"""
    question = clean_question(text)
    return ReadingRequest(kind=kind, formation=formation, fixed=fixed,
                          question=question, topic=norm_topic(question))


def build_request(kind: str = "auto", text: str = "", *,
                  daily_enabled: bool = True,
                  source: str = "command",
                  formation_hint: str = "") -> ReadingRequest:
    """把入口收到的原话解析成 ReadingRequest。

    Args:
        kind: auto（按意图判定）/ single（强制单抽）/ daily（强制今日牌运）。
        text: 用户原话。
        daily_enabled: output.daily_fixed；关闭时普通问法不再路由到固定牌运。
        source: command / tool。只有 tool 会采纳 formation_hint。
        formation_hint: 自然语言入口里模型顺手填的牌阵名，必须在 FORMATIONS 里；
            规则已经识别出阵型时忽略它。

    Returns:
        结构化的占卜请求；帮助请求返回 kind=help。
    """
    text = (text or "").strip()
    if is_help_request(text):
        return ReadingRequest(KIND_HELP, "", False, "", "")
    if kind == KIND_SINGLE:
        return _make(KIND_SINGLE, "羽签", False, text)
    if kind == KIND_DAILY:
        return _make(KIND_DAILY, "羽签", bool(daily_enabled), text)
    if daily_enabled and is_daily_request(text):
        return _make(KIND_DAILY, "羽签", True, text)
    formation, confident = _decide_formation(text)
    hint = (formation_hint or "").strip()
    if source == "tool" and not confident and hint in FORMATIONS:
        formation = hint
    return _make(KIND_SPREAD, formation, False, text)
