"""配置语义层：Settings（冻结 dataclass）+ parse()，默认值只在这里写一次。

config.py 只提供读取原语（分组优先 / 扁平回退 / 类型转换）；
「默认值是什么、旧字段怎么迁移、解析成什么样子」全在这一层。
消费方拿 `parse(config)` 返回的 `Settings`，不再各自对着原始配置问第二遍。

TarotSettings 是迁移期的兼容视图（扁平字段），第 3/4 步消费者改完后删除。
"""
import decimal
import math
from dataclasses import dataclass, field

from .config import _cfg_bool, _cfg_get, _cfg_int

# ---- 默认值常量：集中管理散落的默认值 / 间隔（改默认值只找这一处） ----
DEFAULT_SEGMENT_SIZE = 300      # 无标记文本兜底分段长度
MIN_SEGMENT_SIZE = 50           # 分段长度下限
DEFAULT_AI_TIMEOUT = 30         # 单次 AI 解读超时（秒）；0 = 不限制单个请求
MIN_AI_TIMEOUT = 5              # 非 0 取值的下限：不足 5 秒按 5 秒算（0 是特例，表示不限制）
DEFAULT_AI_BUDGET = 120         # 单次占卜的 AI 总预算（秒，ai.ai_budget_seconds）：所有 AI 阶段共用一个截止点
DEFAULT_AI_PARALLEL_STAGES = True  # 牌灵的话与解读两段 AI 并发（ai.parallel_stages）：关掉则顺序执行
MAX_AI_BUDGET = 24 * 3600       # 预算上限（秒）：一次占卜超过一天没有意义，再大也只会让截止点算不出来
AI_BUDGET_MIN_ATTEMPT = 1.0     # 剩余预算不足此值（秒）就不再发起新请求——注定超时，只是白等一次
DEFAULT_SPIRIT_STAGE_BUDGET = 20  # 牌灵的话的阶段上限（秒）：次要内容不许独吞整笔预算
DEFAULT_DISCLAIMER = "✨ 占卜仅供娱乐参考，选择权永远在你手里。"  # 免责声明默认文案（output.disclaimer，空字符串=不显示）
DEFAULT_AI_COOLDOWN = 60        # AI 解读失败后的冷却（秒），按 Provider 粒度：挂掉的模型只冷却自己，其余照常服务
DEFAULT_AI_PERSONA = "random"   # 牌灵人设（ai.persona）：off=中立 / tsundere 傲娇 / gentle 温柔 / mystic 神秘 / random 每次随机
PERSONA_VALUES = ("off", "tsundere", "gentle", "mystic", "random")  # 与 _conf_schema.json 的 options 一一对应
# 解读结构校验强度（ai.structure_check）：loose=只按宽松口径（标记段数≥牌数），
# shadow=照旧放行但把严格口径的判定记一行（先量再收紧，默认），strict=按严格口径拒候选。
# 严格口径的三条判据见 ai/parse.py（迁移后）；当前仍在 hardening.validate_interpret_structure_strict。
STRUCTURE_CHECK_LOOSE = "loose"
STRUCTURE_CHECK_SHADOW = "shadow"
STRUCTURE_CHECK_STRICT = "strict"
DEFAULT_STRUCTURE_CHECK = STRUCTURE_CHECK_SHADOW
STRUCTURE_CHECK_VALUES = (STRUCTURE_CHECK_LOOSE, STRUCTURE_CHECK_SHADOW,
                          STRUCTURE_CHECK_STRICT)  # 与 _conf_schema.json 的 options 一一对应
DEFAULT_DAILY_CARD = True       # 今日牌运卡（output.daily_card）：/今日牌运 输出竖版卡片图，关闭则回到普通牌面图
DEFAULT_TOOL_COOLDOWN = 60      # 自然语言占卜节流（秒），同会话冷却期内不重复触发
DEFAULT_CMD_RATE_LIMIT = 10     # /占卜、/单抽、/今日牌运 命令入口的同会话节流（秒），0=关闭
DEFAULT_DAILY_COUNT = 0         # 每用户每日占卜次数上限，0=关闭（默认不开，防止升级即限流）
DEFAULT_QUESTION_MAX_LEN = 200  # 送 AI 的问题长度上限（字符，0=不压缩）：ai.question_max_len
DEFAULT_SEND_MODE = "forward"   # 输出呈现模式（见 SEND_MODES）：默认合并转发，占卜结果一条消息收齐
SEND_MODES = ("plain", "forward", "text_only")  # 与 _conf_schema.json 的 options 一一对应


@dataclass(frozen=True)
class AiSettings:
    """ai 组：模型调用、超时、预算与人设。"""

    enable_ai: bool = True
    provider_id: str = ""
    timeout: int = DEFAULT_AI_TIMEOUT
    fail_cooldown: int = DEFAULT_AI_COOLDOWN
    budget_seconds: int = DEFAULT_AI_BUDGET
    parallel_stages: bool = DEFAULT_AI_PARALLEL_STAGES
    persona: str = DEFAULT_AI_PERSONA
    question_max_len: int = DEFAULT_QUESTION_MAX_LEN
    structure_check: str = DEFAULT_STRUCTURE_CHECK


@dataclass(frozen=True)
class ToolSettings:
    """tool 组：自然语言入口与命令节流。"""

    llm_enabled: bool = True
    llm_cooldown: int = DEFAULT_TOOL_COOLDOWN
    cmd_rate_limit: int = DEFAULT_CMD_RATE_LIMIT


@dataclass(frozen=True)
class LimitSettings:
    """limit 组：每用户每日次数。"""

    daily_count: int = DEFAULT_DAILY_COUNT


@dataclass(frozen=True)
class OutputSettings:
    """output 组：发送模式、分段、洗牌提示、免责声明与每日牌运。"""

    send_mode: str = DEFAULT_SEND_MODE
    segment_size: int = DEFAULT_SEGMENT_SIZE
    daily_fixed: bool = True
    daily_card: bool = DEFAULT_DAILY_CARD
    shuffle_lines: bool = True
    disclaimer: str = DEFAULT_DISCLAIMER


@dataclass(frozen=True)
class Settings:
    """一份配置的解析结果：四组值，不可变。"""

    ai: AiSettings = field(default_factory=AiSettings)
    tool: ToolSettings = field(default_factory=ToolSettings)
    limit: LimitSettings = field(default_factory=LimitSettings)
    output: OutputSettings = field(default_factory=OutputSettings)


def resolve_send_mode(config) -> str:
    """输出呈现模式（output.send_mode），旧配置自动迁移（可单测的纯函数）。

    读取优先级：新 key send_mode > 旧 forward_result / show_image 迁移 > 默认 forward。
    旧配置迁移映射（forward_result > show_image，转发即含图）：
      forward_result=true            → forward（忽略图开关）
      show_image=false（且未开转发） → text_only
      默认                           → forward
    新 key 一旦命中立即采用（包括空串等非三态值走默认兜底），
    避免旧扁平字段残留覆盖用户后来在面板上的选择。

    Args:
        config: 原始 AstrBotConfig 或等价的扁平/分组 dict。

    Returns:
        plain / forward / text_only 之一。
    """
    raw = _cfg_get(config, "output", "send_mode", None)
    if raw is None:
        if _cfg_bool(config, "output", "forward_result", False):
            mode = "forward"
        elif not _cfg_bool(config, "output", "show_image", True):
            mode = "text_only"
        else:
            mode = DEFAULT_SEND_MODE
    else:
        mode = str(raw).strip().lower()
    return mode if mode in SEND_MODES else DEFAULT_SEND_MODE


def _budget_seconds_float(raw, default: int = DEFAULT_AI_BUDGET) -> float:
    """AI 总预算归一的浮点版：**只有显式 0 = 不限制**，其余坏值一律回退默认值。

    坏值的完整清单（2026-10-17 两轮复核）：负数（含下溢成 ``-0.0`` 的极小负数）、
    NaN / inf、布尔、超大整数（``float()`` 溢出）、超出 MAX_AI_BUDGET 的值、
    非数字。下溢成正零的极小正数（``"1e-9999"``）同样回退默认：它看着是正数，
    float() 却给出 0.0，只有「真的写成 0」才是显式不限制。

    这里保留小数精度：内部预算对象（AiBudget）的 1.2 秒就是 1.2 秒；
    「至少 1 秒」是配置层的口径，落在下面的 _budget_seconds 里。
    """
    if isinstance(raw, bool):
        return float(default)
    try:
        num = float(raw)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    if math.isnan(num) or math.isinf(num):
        return float(default)
    # -0.0 要单独判：`-0.0 < 0` 是 False，漏掉就会把极小负数当成显式 0
    if num < 0 or (num == 0 and math.copysign(1.0, num) < 0):
        return float(default)
    if num == 0:
        # 区分「显式 0」与「下溢成 0」：字符串交给 Decimal 看原值。
        if isinstance(raw, str):
            try:
                if decimal.Decimal(raw.strip()) != 0:
                    return float(default)
            except (decimal.InvalidOperation, ValueError):
                return float(default)
        return 0.0
    # 有限但巨大的值（如 "1e308"）转成整数后加不进 float 时钟：
    # AiBudget 构造时 clock() + seconds 会 OverflowError，把所有占卜一起带走。
    if num > MAX_AI_BUDGET:
        return float(default)
    return num


def _budget_seconds(raw, default: int = DEFAULT_AI_BUDGET) -> int:
    """配置层归一：浮点校验 + 「非 0 至少按 1 秒算」（0.5 秒不能截成 0=不限制）。"""
    num = _budget_seconds_float(raw, default)
    if num <= 0:
        return 0
    return max(1, int(num))


def _is_negative_number(raw) -> bool:
    """原始配置值是不是负数（含 -0.5 这种取整后变 0 的）。坏值 / 布尔不算。"""
    if isinstance(raw, bool):
        return False
    try:
        return float(raw) < 0
    except (TypeError, ValueError, OverflowError):
        return False


def parse(config) -> Settings:
    """把一份原始配置解析成 Settings：默认值、旧字段迁移与坏值归一都在这里。

    Args:
        config: 原始 AstrBotConfig；None 表示全默认。

    Returns:
        冻结的 Settings（ai / tool / limit / output 四组）。
    """
    # 单次 AI 解读超时（秒）：**0 = 不限制单个请求**；其余取值一律不低于
    # MIN_AI_TIMEOUT（5 秒）——1~4 秒那种「请求还没发出去就注定超时」的设置不是
    # 自由度，是自残；负数更是坏值。下限只对非 0 值生效：0 是「关掉这一档」的开关。
    _raw_timeout = _cfg_int(config, "ai", "ai_timeout", DEFAULT_AI_TIMEOUT)
    ai_timeout = (_raw_timeout if _raw_timeout == 0
                  else max(MIN_AI_TIMEOUT, _raw_timeout))
    # 送 AI 的问题长度上限（字符，0=不压缩）：负数不是「更不压缩」而是坏值，
    # 与预算同口径回默认，0 才表示明确关闭截断。
    _max_len_raw = _cfg_get(config, "ai", "question_max_len", DEFAULT_QUESTION_MAX_LEN)
    _max_len = _cfg_int(config, "ai", "question_max_len", DEFAULT_QUESTION_MAX_LEN)
    ai_max_len = (DEFAULT_QUESTION_MAX_LEN
                  if _max_len < 0 or (_max_len == 0 and _is_negative_number(_max_len_raw))
                  else _max_len)
    # 牌灵人设（ai.persona）：非法值回退默认（off=恢复中立语气）。
    persona = str(_cfg_get(config, "ai", "persona", DEFAULT_AI_PERSONA) or "").strip().lower()
    if persona not in PERSONA_VALUES:
        persona = DEFAULT_AI_PERSONA
    # 解读结构校验强度（ai.structure_check）：非法值回退默认（shadow=先量不拦）。
    structure_check = str(
        _cfg_get(config, "ai", "structure_check", DEFAULT_STRUCTURE_CHECK) or "").strip().lower()
    if structure_check not in STRUCTURE_CHECK_VALUES:
        structure_check = DEFAULT_STRUCTURE_CHECK
    return Settings(
        ai=AiSettings(
            enable_ai=_cfg_bool(config, "ai", "enable_ai", True),
            provider_id=str(_cfg_get(config, "ai", "ai_provider", "") or "").strip(),
            timeout=ai_timeout,
            fail_cooldown=_cfg_int(config, "ai", "ai_fail_cooldown", DEFAULT_AI_COOLDOWN),
            # 预算归一只认显式 0（不限制），其余坏值回默认；别让取整/溢出把保护关掉。
            budget_seconds=_budget_seconds(
                _cfg_get(config, "ai", "ai_budget_seconds", DEFAULT_AI_BUDGET)),
            parallel_stages=_cfg_bool(config, "ai", "parallel_stages",
                                      DEFAULT_AI_PARALLEL_STAGES),
            persona=persona,
            question_max_len=ai_max_len,
            structure_check=structure_check,
        ),
        tool=ToolSettings(
            llm_enabled=_cfg_bool(config, "tool", "llm_tool_enabled", True),
            llm_cooldown=_cfg_int(config, "tool", "llm_tool_cooldown", DEFAULT_TOOL_COOLDOWN),
            cmd_rate_limit=_cfg_int(config, "tool", "cmd_rate_limit", DEFAULT_CMD_RATE_LIMIT),
        ),
        limit=LimitSettings(
            daily_count=_cfg_int(config, "limit", "daily_count", DEFAULT_DAILY_COUNT),
        ),
        output=OutputSettings(
            send_mode=resolve_send_mode(config),
            segment_size=_cfg_int(config, "output", "segment_size",
                                  DEFAULT_SEGMENT_SIZE, floor=MIN_SEGMENT_SIZE),
            daily_fixed=_cfg_bool(config, "output", "daily_fixed", True),
            daily_card=_cfg_bool(config, "output", "daily_card", DEFAULT_DAILY_CARD),
            shuffle_lines=_cfg_bool(config, "output", "shuffle_lines", True),
            disclaimer=str(_cfg_get(config, "output", "disclaimer", DEFAULT_DISCLAIMER)),
        ),
    )


class TarotSettings:
    """迁移期兼容视图：把嵌套 Settings 摊平成旧字段名。

    StarTarot / AiInterpreter / Deliverer 仍按扁平字段消费；第 3/4 步把它们改成
    直接用 Settings 后，本类删除。
    """

    def __init__(self, config):
        s = parse(config)
        self.enable_ai = s.ai.enable_ai
        self.segment_size = s.output.segment_size
        self.send_mode = s.output.send_mode
        self.shuffle_lines = s.output.shuffle_lines
        self.disclaimer = s.output.disclaimer
        self.daily_fixed = s.output.daily_fixed
        self.daily_card = s.output.daily_card
        self.ai_timeout = s.ai.timeout
        self.ai_cooldown = s.ai.fail_cooldown
        self.ai_budget = s.ai.budget_seconds
        self.ai_parallel_stages = s.ai.parallel_stages
        self.llm_tool_enabled = s.tool.llm_enabled
        self.llm_tool_cooldown = s.tool.llm_cooldown
        self.cmd_rate_limit = s.tool.cmd_rate_limit
        self.daily_count_limit = s.limit.daily_count
        self.ai_provider_id = s.ai.provider_id
        self.ai_max_len = s.ai.question_max_len
        self.ai_persona = s.ai.persona
        self.structure_check = s.ai.structure_check
