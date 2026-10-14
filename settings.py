"""配置语义层：全部默认值、旧配置迁移与 TarotSettings（一份配置的解析结果）。

config.py 只提供读取原语（分组优先 / 扁平回退 / 类型转换）；
「默认值是什么、旧字段怎么迁移、解析成什么样子」全在这一层。
StarTarot / AiInterpreter / Deliverer 只消费解析后的数值，不碰配置结构。

——默认值、迁移规则都只在这里维护，其他地方别自作主张。
"""
from .config import _cfg_bool, _cfg_get, _cfg_int

# ---- 默认值常量：集中管理散落的默认值 / 间隔（改默认值只找这一处） ----
DEFAULT_SEGMENT_SIZE = 300      # 无标记文本兜底分段长度
MIN_SEGMENT_SIZE = 50           # 分段长度下限
DEFAULT_AI_TIMEOUT = 30         # 单次 AI 解读超时（秒）；0 = 不限制单个请求
DEFAULT_AI_BUDGET = 120         # 单次占卜的 AI 总预算（秒，ai.ai_budget_seconds）：所有 AI 阶段共用一个截止点
AI_BUDGET_MIN_ATTEMPT = 1.0     # 剩余预算不足此值（秒）就不再发起新请求——注定超时，只是白等一次
DEFAULT_SPIRIT_STAGE_BUDGET = 20  # 牌灵的话的阶段上限（秒）：次要内容不许独吞整笔预算
DEFAULT_DISCLAIMER = "✨ 占卜仅供娱乐参考，选择权永远在你手里。"  # 免责声明默认文案（output.disclaimer，空字符串=不显示）
DEFAULT_AI_COOLDOWN = 60        # AI 解读失败后的冷却（秒），按 Provider 粒度：挂掉的模型只冷却自己，其余照常服务
DEFAULT_AI_PERSONA = "random"   # 牌灵人设（ai.persona）：off=中立 / tsundere 傲娇 / gentle 温柔 / mystic 神秘 / random 每次随机
PERSONA_VALUES = ("off", "tsundere", "gentle", "mystic", "random")  # 与 _conf_schema.json 的 options 一一对应
# 解读结构校验强度（ai.structure_check）：loose=只按宽松口径（标记段数≥牌数），
# shadow=照旧放行但把严格口径的判定记一行（先量再收紧，默认），strict=按严格口径拒候选。
# 严格口径的三条判据见 hardening.validate_interpret_structure_strict。
# 影子档量两件事（缺一不可，口径见 interpret._log_shadow_summary）：① 候选拒绝率；
# ② 「按严格口径这一次会不会整段降级」——按候选链的三个出口给三态判决：链中出现严格
# 通过者=不降级、链已观测完且无严格通过者=降级、因宽松放行提前收尾=未观测（不硬报降级）。
STRUCTURE_CHECK_LOOSE = "loose"
STRUCTURE_CHECK_SHADOW = "shadow"
STRUCTURE_CHECK_STRICT = "strict"
DEFAULT_STRUCTURE_CHECK = STRUCTURE_CHECK_SHADOW
STRUCTURE_CHECK_VALUES = (STRUCTURE_CHECK_LOOSE, STRUCTURE_CHECK_SHADOW,
                          STRUCTURE_CHECK_STRICT)  # 与 _conf_schema.json 的 options 一一对应
DEFAULT_DAILY_CARD = True       # 今日牌运卡（output.daily_card）：/单抽 与运势请求输出竖版卡片图，关闭则回到普通牌面图
DEFAULT_TOOL_COOLDOWN = 60      # 自然语言占卜节流（秒），同会话冷却期内不重复触发
DEFAULT_CMD_RATE_LIMIT = 10     # /占卜、/单抽 命令入口的同会话节流（秒），0=关闭
DEFAULT_DAILY_COUNT = 0         # 每用户每日占卜次数上限，0=关闭（默认不开，防止升级即限流）
DEFAULT_QUESTION_MAX_LEN = 200  # 送 AI 的问题长度上限（字符，0=不压缩）：ai.question_max_len
DEFAULT_SEND_MODE = "forward"   # 输出呈现模式（见 SEND_MODES）：默认合并转发，占卜结果一条消息收齐
SEND_MODES = ("plain", "forward", "text_only")  # 与 _conf_schema.json 的 options 一一对应


def resolve_send_mode(config) -> str:
    """输出呈现模式（output.send_mode），旧配置自动迁移（可单测的纯函数）。

    读取优先级：新 key send_mode > 旧 forward_result / show_image 迁移 > 默认 forward。
    旧配置迁移映射（forward_result > show_image，转发即含图）：
      forward_result=true            → forward（忽略图开关）
      show_image=false（且未开转发） → text_only
      默认                           → forward
    新 key 一旦命中立即采用（包括空串等非三态值走默认兜底），
    避免旧扁平字段残留覆盖用户后来在面板上的选择。
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


class TarotSettings:
    """一份配置的解析结果：字段即插件运行所需的全部配置值。

    分组读取（ai / tool / output / limit），旧扁平配置自动兼容；
    各字段含义与默认值见上方 DEFAULT_* 常量与 _conf_schema.json。
    """

    def __init__(self, config):
        """把一份配置解析成插件真正用得上的字段——所有默认值/迁移规则
        都在 settings 层，解析完的数值不对着配置继续问第二遍。"""
        self.enable_ai = _cfg_bool(config, "ai", "enable_ai", True)
        self.segment_size = _cfg_int(config, "output", "segment_size",
                                     DEFAULT_SEGMENT_SIZE, floor=MIN_SEGMENT_SIZE)
        # 输出组：发送模式 / 洗牌提示语 / 免责声明（呈现方式三件套）
        self.send_mode = resolve_send_mode(config)
        self.shuffle_lines = _cfg_bool(config, "output", "shuffle_lines", True)
        self.disclaimer = str(_cfg_get(config, "output", "disclaimer", DEFAULT_DISCLAIMER))
        # 今日固定牌运开关：抽取规则归属 spreads/daily，开关归属这里
        self.daily_fixed = _cfg_bool(config, "output", "daily_fixed", True)
        # 今日牌运卡开关：/单抽 与运势请求输出竖版海报卡，关闭则回退普通牌面图
        self.daily_card = _cfg_bool(config, "output", "daily_card", DEFAULT_DAILY_CARD)
        # 单次 AI 解读超时（秒）：0 = 不限制单个请求（2026-10-14 主人定）。从前 0 与
        # 1~4 都会被静默夹成 5 秒，面板上「0 = 不限制」这类直觉读不通；现在 0 就是 0。
        # 代价可控：总预算（ai_budget_seconds）与牌灵的话的阶段上限都还在，
        # 它们是独立的第二、第三约束——不会因为一个 0 就让 AI 阶段彻底没有上界。
        # 负数同样按 0 处理（floor=0，与 ai_budget_seconds 同一口径）。
        self.ai_timeout = _cfg_int(config, "ai", "ai_timeout", DEFAULT_AI_TIMEOUT, floor=0)
        self.ai_cooldown = _cfg_int(config, "ai", "ai_fail_cooldown", DEFAULT_AI_COOLDOWN)
        # 单次占卜的 AI 总预算（秒）：0 = 不限制（显式关掉总预算保护，AiInterpreter
        # 构造时会打一条 warning）。管的是「AI 阶段」的等待上界，不是整条消息端到端
        # 的上界——图片渲染、框架调度、平台发送各有各的耗时。
        self.ai_budget = _cfg_int(config, "ai", "ai_budget_seconds", DEFAULT_AI_BUDGET, floor=0)
        # 自然语言入口开关：默认值在 settings，工具注册是静态的，开关只在运行期判断（见 main.divine_tool）
        self.llm_tool_enabled = _cfg_bool(config, "tool", "llm_tool_enabled", True)
        self.llm_tool_cooldown = _cfg_int(config, "tool", "llm_tool_cooldown",
                                          DEFAULT_TOOL_COOLDOWN)
        # 命令入口会话级节流（/占卜、/单抽）：tool.cmd_rate_limit，0=关闭
        self.cmd_rate_limit = _cfg_int(config, "tool", "cmd_rate_limit", DEFAULT_CMD_RATE_LIMIT)
        # 每用户每日次数上限（三入口统一）：limit.daily_count，0=关闭
        self.daily_count_limit = _cfg_int(config, "limit", "daily_count", DEFAULT_DAILY_COUNT)
        # 指定 AI 解读模型（provider id，留空 = 用当前会话模型）
        self.ai_provider_id = str(_cfg_get(config, "ai", "ai_provider", "") or "").strip()
        # 牌灵人设（ai.persona）：非法值回退默认（off=恢复中立语气）
        self.ai_persona = str(_cfg_get(config, "ai", "persona", DEFAULT_AI_PERSONA) or "").strip().lower()
        if self.ai_persona not in PERSONA_VALUES:
            self.ai_persona = DEFAULT_AI_PERSONA
        # 送 AI 的问题长度上限（字符，0=不压缩）：ai.question_max_len
        self.ai_max_len = _cfg_int(config, "ai", "question_max_len", DEFAULT_QUESTION_MAX_LEN)
        # 解读结构校验强度（ai.structure_check）：非法值回退默认（shadow=先量不拦）
        self.structure_check = str(
            _cfg_get(config, "ai", "structure_check", DEFAULT_STRUCTURE_CHECK) or "").strip().lower()
        if self.structure_check not in STRUCTURE_CHECK_VALUES:
            self.structure_check = DEFAULT_STRUCTURE_CHECK
