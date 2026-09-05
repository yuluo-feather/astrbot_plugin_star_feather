"""牌阵与问题清洗：用户文本 → 选哪个阵、问题主体是什么。

- 选阵：显式指定阵名 > 关键词权重累计 > 内容推断 > 兜底（羽时三刻）
- 清洗：剥阵名（独立词边界）、剥祈使词，得到送给 AI 的问题主体

tarot_core 只消费结果（formation / clean question），不关心匹配规则；
规则独立在此，方便扩展新阵型与单测。
（说白了：牌灵挑阵的规矩，都写在这里，想加阵型也在这加。）
"""
import re

# 牌阵定义：牌位列表（张数取 len）
FORMATIONS = {
    "羽签": ["你的当下"],
    "羽时三刻": ["过去", "现在", "未来"],
    "羽镜": ["现状", "阻碍", "建议"],
    "恋羽十字": ["你", "对方", "关系现状", "未来走向"],
}

# 语义关键词 → 牌阵（权重累计，阵名词见 FORMATION_ALIASES）
# 注意：运势类词（运势/运气/牌运/日运/每日/近期/最近）不在此列——
# 它们由 daily.py 的 DAILY_WORDS 先行判定（今日固定牌运），
# 只有 daily_fixed 关闭时才落到本层/兜底；这里收录会误导成「还能选别的阵」。
KEYWORD_MAP = [
    (("情感", "爱情", "恋爱", "喜欢", "感情", "分手", "复合"), "恋羽十字"),
    (("事业", "工作", "面试", "学业", "考试", "考研", "升职"), "羽镜"),
    (("过去", "未来", "时间线"), "羽时三刻"),
]

# 关系语义词（选阵推断层与「时间线词冲突」判定共用）：
# 他/她/我们 是推断层主体词；感情类词与 KEYWORD_MAP 感情组重叠（那组命中时
# 结果已是恋羽十字，这里不改变行为，只是同一类语义一处维护）。
RELATION_WORDS = ("他", "她", "我们", "喜欢我吗", "还爱",
                  "感情", "喜欢", "恋爱", "复合", "分手", "爱情")

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
    r"^(?:帮我|给我|麻烦|请|求你|来|算算|算一算|算一卦|来一卦|抽一签|抽一支签|"
    r"抽一张牌|抽一张|抽个|抽牌|抽签|看看|测测|占卜一下|占卜)\s*"
)


def select_formation(text: str) -> str:
    """三层决策：显式指定阵名 > 关键词权重累计 > 内容推断 > 兜底。"""
    text = (text or "").strip()
    for alias, name in FORMATION_ALIASES.items():
        if alias in text:
            return name
    best_name, best_score = None, 0
    for keywords, name in KEYWORD_MAP:
        score = sum(1 for k in keywords if k in text)
        if score > best_score:  # 严格大于：平局保持 KEYWORD_MAP 顺序优先级
            best_name, best_score = name, score
    if best_name:
        # 时间线词 + 关系语义 = 关系预测（「我们的未来」「她未来会爱我吗」），
        # 不是纯时间线问题——时间线阵只留给无关系主体的问法（「我的未来」）。
        if best_name == "羽时三刻" and any(k in text for k in RELATION_WORDS):
            return "恋羽十字"
        return best_name
    if any(k in text for k in RELATION_WORDS):
        return "恋羽十字"
    return "羽时三刻"


def strip_alias(text: str) -> str:
    """剥离「作为独立词出现」的阵名（前后为空白/标点/边界），避免干扰 AI 语义；
    句中动词用法如「单抽一张牌」不受影响（选阵层保留宽松匹配，此处才收紧）。"""
    text = text or ""
    for pat, _ in _ALIAS_PATTERNS:
        text = pat.sub("", text)
    return re.sub(r"[ 	]{2,}", " ", text).strip(" 	，。、！？：；,.!?;:")


def clean_tool_question(text: str) -> str:
    """自然语言入口的问题清洗：剥掉句首祈使词与阵名，保留问题主体。

    「帮我算一卦我和她的感情」→「我和她的感情」；剥完为空说明用户没给具体
    问题（比如只说了「帮我看一卦」），由调用方用默认兜底语句。
    """
    text = strip_alias(text or "")
    while True:
        new = REQUEST_WORDS.sub("", text.strip())
        if new == text:
            break
        text = new
    return text.strip("，,。.！!？? ")
