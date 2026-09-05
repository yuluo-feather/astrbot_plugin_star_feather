"""星羽塔罗插件入口：命令注册、三入口编排。其他事都分出去了，这里只管进门。

每个模块负责什么，按域分组一次说清楚——别指望我讲第二遍：

- 配置层：settings.py（全部默认值与旧配置迁移）、config.py（读取原语）、identity.py（事件身份标识）
- 占卜核心：spreads.py（选阵与问题清洗）、tarot_core.py（抽牌与牌义）、tarot_data.py（牌库）、daily.py / dailylines.py（今日固定牌运 / 每日签文池）
- AI 解读：interpret.py（候选链与超时）、hardening.py（Prompt 防护）、prompts.py（提示词与全部文案）
- 输出与兜底：deliver.py（发送编排）、card_render.py / fonts.py（渲染与字体）、log_setup.py（运行日志落盘）
- 限流与存储：gating.py / limiter.py（限流闸门与纯逻辑）、kv_utils.py（KV 读写原语，daily/gating 共用）
"""
# ruff: noqa: F403, F405  # 星导入是 AstrBot 插件惯例，名字进来自框架，静态分析无从溯源
import logging
import os
import random
import re
import sys

# 插件被动态 __import__ 加载时不在 sys.path 里，得自己把插件目录塞进去——不然 import 直接炸。
# 守卫写法：已在则跳过，热重载时不重复累积 sys.path 条目。
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

# 重载防御：AstrBot 重载插件时只卸载星模块（data.plugins.astrbot_plugin_star_feather），
# 顶层子模块（prompts 等）会赖在 sys.modules 里不走——旧模块没有新名字（如 TOOL_EPILOGUE）
# 时，from prompts import ... 直接 ImportError。所以在导入前无条件清掉本插件子模块，
# 保证每次加载的都是磁盘上的最新代码，不给我留僵尸模块。
for _sf_mod in ("config", "daily", "dailylines", "gating", "hardening", "identity", "interpret",
                "limiter", "log_setup", "prompts", "settings", "spreads",
                "tarot_core", "tarot_data", "card_render", "deliver", "fonts"):
    sys.modules.pop(_sf_mod, None)

# 本插件的模块间引用（实现分别在 config / daily / gating / identity / prompts / settings / spreads）：
# 测试与外部如需内部函数，请直接从实现模块导入，别绕道本入口文件转发。
from astrbot.api.all import *
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain

from card_render import cleanup_stale_images
from daily import DailyFortune, _is_daily_request
from gating import LimitGate
from identity import resolve_sender_uid
from prompts import (
    HELP_TEXT,
    RESULT_EPILOGUE,
    SHUFFLE_LINES,
    TOOL_EPILOGUE,
    resolve_persona,
)
from spreads import clean_tool_question, select_formation
from tarot_core import StarTarot

logger = logging.getLogger(__name__)

VERSION = "0.6.0"

# 帮助请求判定：整句剥掉祈使词后只剩「帮助 / help / 说明」才算。
# 旧版用 "帮助" in text，问题正文里带「帮助」（如「帮助我做出决定」）
# 会误触发帮助页——踩过：占卜长问题里写「帮助我做出更清醒的决定」，
# 用户收到的却是帮助文案。这里是收紧后的整句匹配，句中出现不算。
# 2026-08-29 语料回归补充：
# - 核心词与「一下」间允许空白（"help 一下"）；前缀扩入 说/讲/告诉/教 及 说下/讲下 等连缀；
# - 独立宽松分支 _HELP_USAGE_RE：句中含「怎么用/怎么玩/怎么使用/怎么操作/用法」即算——
#   这类功能问法引导词千变万化（告诉你怎么用/教我一下/这功能怎么玩），
#   枚举祈使前缀不现实，改为按「功能意图词」判定，误伤率低（占卜问题里几乎不出现）。
_HELP_REQUEST_RE = re.compile(
    r"^(?:请|帮我|给我|麻烦|来|看看|看下|查一下|查查|使用|查看|瞅瞅|"
    r"说|讲|告诉|教|说下|讲下|说一下|讲一下|说说|讲讲)*\s*"
    r"(?:帮助|help|帮助说明|使用说明|说明)(?:\s*(?:一下|下))?\s*[？?！!。.]*$",
    re.IGNORECASE,
)
_HELP_USAGE_RE = re.compile(
    r"^[^？?！!。]*(?:怎么用|怎么玩|怎么使用|怎么操作|用法)[？?！!。]*$"
)

# 牌阵定义见 spreads.py（FORMATIONS）

@register("star_feather", "羽落", "星羽塔罗：78张塔罗牌 AI 占卜与深度解读，官方素材渲染牌面图", VERSION)
class StarFeatherPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.tarot = StarTarot(context, config)
        # kv_store 必须传插件实例自身（Star 基类混入 PluginKVStoreMixin 提供
        # get/put_kv_data）；传 AstrBot Context 会因没有这俩方法导致缓存静默失效。
        # 上次吃亏就吃在这，别重蹈覆辙哦。
        self.daily = DailyFortune(self, self.tarot)
        # 自然语言入口开关：llm_tool 装饰器是 import 时静态注册的，没法随配置动态卸载，
        # 所以在函数里做运行期判断：工具照样注册，但开关关了就回提示，不进占卜流程。
        # 开关的默认值与读取收敛在 settings.TarotSettings（见 self.tarot.llm_tool_enabled）。
        # 今日固定牌运开关：核心在初始化时已从 output.daily_fixed 读取，这里直接取用
        self.daily_fixed = self.tarot.daily_fixed
        # 今日牌运卡开关（output.daily_card）：关闭时 /单抽 与运势请求回退普通牌面图
        self.daily_card = self.tarot.daily_card
        # 限流闸门：KV 粘合与拦截文案在 gating.py，纯逻辑在 limiter.py
        self.gate = LimitGate(self, self.tarot.cmd_rate_limit, self.tarot.daily_count_limit)
        # 启动清空渲染临时目录：防止上次进程遗留的牌面图无限堆积（磁盘兜底）
        cleanup_stale_images()

    async def initialize(self):
        """实例化后的异步初始化钩子：打印启动横幅，本羽的排面不能少。"""
        logger.info(fr"""
 ____  _               _____          _   _
/ ___|| |_ __ _ _ __  |  ___|__  __ _| |_| |__   ___ _ __
\___ \| __/ _` | '__| | |_ / _ \/ _` | __| '_ \ / _ \ '__|
 ___) | || (_| | |    |  _|  __/ (_| | |_| | | |  __/ |
|____/ \__\__,_|_|    |_|  \___|\__,_|\__|_| |_|\___|_|
星羽塔罗 v{VERSION} · 78张牌已就位，牌灵在等你来问。
""")

    def _shuffle_hint(self) -> str | None:
        """洗牌提示语：output.shuffle_lines 关闭时返回 None，调用方不发。
        哼，仪式感这种东西，想关就关，但本羽默认是开着的。"""
        if not self.tarot.shuffle_lines:
            return None
        return random.choice(SHUFFLE_LINES)

    def _require_prefix(self, event: AstrMessageEvent) -> bool:
        """触发门槛：信任框架，不再自行校验前缀。

        能走到这里说明 CommandFilter 已命中本命令——框架的 waking_check
        （wake_prefix 触发词 / @ 机器人 / 私聊唤醒）先于插件完成了门槛判定，
        插件重复检查前缀只会和 WebUI 配置的触发词打架（原「私聊必须带 /」
        正是如此）。防误触由框架保证：命令名需以
        「占卜 问题」整段开头（后跟空格或句尾），「占卜一下」这类闲聊措辞
        不会被当成命令。哼，门槛交给框架，牌灵只管算牌。

        注意：本方法恒为 True，是「不再收紧门槛」的设计哨兵（见 tests 的
        TestRequirePrefix 防回归）；不要把它改回真正的校验。
        """
        return True

    async def _command_entry(self, event: AstrMessageEvent, text: str, *,
                             err_tpl: str, helpable: bool = False,
                             force_daily: bool = False, fixed_formation: str = "",
                             fail_note: str = "",
                             empty_fallback: str = "（未提具体问题，请作一般运势解读）",
                             epilogue: bool = False):
        """命令入口公共流程（/占卜 与 /单抽 共用）：帮助 → 限流 → 抽牌 → 发送。

        err_tpl 是完整的用户侧友好文案（不含异常细节——异常只进日志，
        服务器路径/内部结构不给用户看），其余差异由参数控制。
        前缀/触发门槛由框架先行把关（CommandFilter 命中才进得来），
        这里不再有前缀检查与提示分支——别再把「必须带 /」的旧逻辑加回来。
        """
        # 命令已由本插件处理：禁止默认 LLM 参与本次事件。
        # 刻意不用 stop_event()：它在洋葱模型下于响应阶段清空 result 之后才执行，
        # 会 set 出一个空 STOP result → RespondStage 收到空消息（Prepare 空日志）、
        # 收口/统计类插件可能把它误判为「空回复」入账（见 CHANGELOG 命令入口收尾修复）。
        # 这坑踩过了，别再踩。
        event.should_call_llm(True)
        try:
            text_norm = (text or "").strip()
            if helpable and (_HELP_REQUEST_RE.fullmatch(text_norm)
                             or _HELP_USAGE_RE.fullmatch(text_norm)):
                yield event.plain_result(self._help())
                return
            block = await self.gate.check(event, for_command=True)
            if block:
                yield event.plain_result(block)
                return
            # 统一抽牌 + 统一流程：运势类走今日固定，具体问题自由随机
            is_daily, daily_uid, formation, positions, picks = await self._pick_reading(
                event, text, force_daily=force_daily, fixed_formation=fixed_formation)
            async for r in self._run_reading(
                    event, formation, positions, picks, text,
                    is_daily=is_daily, daily_uid=daily_uid,
                    fail_note=fail_note, empty_fallback=empty_fallback,
                    epilogue=True):
                yield r
        except Exception as e:
            logger.error(f"命令占卜失败: {e}", exc_info=True)
            # 异常细节只进日志：err_tpl 是纯友好文案，不把路径/内部结构发给用户
            yield event.plain_result(err_tpl)

    @command("占卜", desc="78张塔罗牌 AI 占卜与深度解读")
    async def divine(self, event: AstrMessageEvent, text: str = ""):
        async for r in self._command_entry(
                event, text,
                err_tpl="哼，这场占卜断了。牌灵今天状态不好，换个时候再来问。",
                helpable=True,
                fail_note="📖 解读：\n（AI 今天闹脾气不肯开口，牌义先给你，自己琢磨~）"):
            yield r

    @command("单抽", desc="随机抽取一张塔罗牌并解读")
    async def single(self, event: AstrMessageEvent, text: str = ""):
        async for r in self._command_entry(
                event, text,
                err_tpl="单抽断线了。牌灵今天不太配合，晚点再试。",
                force_daily=True, fixed_formation="羽签",
                empty_fallback="（今日牌运）"):
            yield r

    async def _tool_send(self, event: AstrMessageEvent, text: str) -> None:
        """llm_tool 入口的文本直发：event.send 独立发送，不走 yield。

        工具内内容若通过 yield MessageEventResult 返回，runner 会收到 None
        （直发分支）而判定"无返回值"，直接结束 Agent Loop——respond 阶段
        空消息被跳过，收口钩子（如账本/统计类插件）不触发，群聊防并发门闩
        锁 300 秒。所以工具入口统一 event.send 直发 + 结尾 yield 字符串。
        （简单说：直发 + 留句话，别让收口卡死。）
        """
        try:
            await event.send(MessageChain([Plain(text)]))
        except Exception as e:
            logger.warning(f"星羽工具直发失败: {e}")

    @filter.llm_tool(name="star_feather_divine")
    async def divine_tool(self, event: AstrMessageEvent):
        """星羽塔罗占卜：78 张塔罗牌抽牌，输出牌面图与解读。

        仅当用户**明确要求**塔罗占卜/抽牌/看运势时调用，例如「帮我算一卦」
        「抽张牌看看」「占卜一下我和她的感情」「最近运势怎么样」。
        用户的抱怨、感叹、闲聊（如「今天真倒霉」「她是不是不喜欢我」）不属于
        占卜请求，不得调用。相邻调用若在节流期（llm_tool_cooldown 秒）内，
        直接向用户说明并结束，不重复占卜。

        发送约定：内容一律 event.send 直发，结尾 yield 一个字符串结果给
        Agent Loop（LLM 据此生成简短收尾回复）。不能 yield MessageEventResult
        或 None —— 否则 runner 判定无返回值并 DONE，respond 空消息跳过，
        收口钩子不触发、群聊防并发门闩死锁（见 _tool_send）。
        """
        # 收尾引导语（prompts 集中管理，随机一条）：让 LLM 生成一句简短确认（同时保住 respond 非空）
        note = random.choice(TOOL_EPILOGUE)
        # 打开开关：运行期判断（装饰器静态注册无法卸载，见 __init__ 注释）
        if not self.tarot.llm_tool_enabled:
            await self._tool_send(event, "星羽塔罗的自然语言占卜未开启～ 试试 /占卜 感情 这样的命令。")
            # 非占卜分支：不 yield 收尾池文案（它说「占卜结果已发送」，这里没有结果），
            # 改为明确引导，让 LLM 自然回一句即可（仍是字符串，保 Agent Loop 不断）
            yield "已告知用户自然语言占卜未开启。请自然回应一句，不必再调用工具。"
            return
        # message_str 属性异常/缺失时按空文本处理（保 Agent Loop 不中断）
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            yield "工具收到空文本：用户没说占卜什么。请回一句，引导用户说出想占卜的内容（如感情、事业、学业、今日运势）。"
            return
        # 工具节流：同会话冷却期内不重复触发，防止模型连续调用刷屏
        umo = getattr(event, "unified_msg_origin", None)
        remain = await self.gate.session_throttle(f"sf_tool_cd_{umo}",
                                                  self.tarot.llm_tool_cooldown)
        if remain > 0:
            await self._tool_send(event, f"牌灵刚忙完一卦，让它歇 {remain} 秒，或者用 /占卜 再来一卦~")
            yield f"已提示用户节流中（剩 {remain} 秒）。请自然回应一句，不必再调用工具。"
            return
        # 每日次数限流（与命令入口同一闸门，仅跳过会话级节流）
        block = await self.gate.check(event, for_command=False)
        if block:
            await self._tool_send(event, block)
            yield "已提示用户今日次数限制。请自然回应一句，不必再调用工具。"
            return
        try:
            # 统一抽牌 + 统一流程（与 /占卜 同一套逻辑，行为一致）
            is_daily, daily_uid, formation, positions, picks = await self._pick_reading(event, text)
            async for r in self._run_reading(event, formation, positions, picks, text,
                                             is_daily=is_daily, daily_uid=daily_uid):
                chain = getattr(r, "chain", None)
                if chain:
                    await event.send(MessageChain(chain=list(chain)))
            yield note
        except Exception as e:
            logger.error(f"自然语言占卜失败: {e}")
            await self._tool_send(event, "这卦起得有点乱……牌灵需要缓缓，换个时候再来问。")
            yield note

    # ---------- 统一抽牌与流程（三入口共用） ----------

    async def _pick_reading(self, event: AstrMessageEvent, text: str,
                            force_daily: bool = False,
                            fixed_formation: str = "") -> tuple:
        """统一抽牌入口：决定「抽什么牌、怎么抽」，三入口共用。

        规则（与文档一致）：
        - 运势类请求（含「运势/运气/牌运」等词，或 /单抽 的 force_daily）
          且开关 daily_fixed 开启 → 今日固定牌运（同一天同一人同一张牌）；
        - 具体问题（感情/事业等）或开关关闭/固定不可用 → 自由随机；
          随机时可用 fixed_formation 锁定阵型（/单抽 固定羽签），否则按问题选阵。

        返回 (is_daily, daily_uid, formation, positions, picks)，
        is_daily=True 时解读走当日缓存，False 走 AI 现场解读。
        """
        daily_uid = resolve_sender_uid(event)
        is_daily = self.daily_fixed and (force_daily or _is_daily_request(text))
        if is_daily:
            picked = await self.daily.pick_cached(daily_uid)
            if picked:
                return True, daily_uid, *picked
            logger.warning("每日牌运不可用（无用户标识），回退自由随机")
        formation = fixed_formation or select_formation(text)  # 选阵看原话，口语不干扰关键词
        positions, picks = self.tarot._draw(formation)
        return False, daily_uid, formation, positions, picks

    async def _run_reading(self, event: AstrMessageEvent, formation: str, positions: list[str],
                           picks: list[dict], question: str, is_daily: bool = False,
                           daily_uid: str = "", fail_note: str = "",
                           empty_fallback: str = "（未提具体问题，请作一般运势解读）",
                           epilogue: bool = False):
        """统一占卜流程：洗牌提示 → 渲染牌面 → 生成解读 → 分段发送。

        解读来源：今日固定且已知用户时走当天解读缓存（没提主题一整天固定）；
        否则 AI 现场解读（失败由 _deliver 回退牌义）。
        洗牌提示语开关（shuffle_lines）、发送模式（send_mode）在此生效，
        所有配置开关只写在这一处，入口无需感知。
        单张牌阵（羽签/单抽）不发洗牌提示：仪式感只属于多张牌阵。
        """
        # 注意：洗牌提示不走 yield —— 命令入口框架只保留最后一个 yield（asyncgen 取末值），
        # 工具入口则由 divine_tool 拦截链结果改 event.send 直发（见 _tool_send），
        # 但 asyncgen 都会被完整迭代，副作用会执行：hint 用 event.send 直接独立发出，
        # 转发结果仍走最后一个 yield，由框架发送。send 异常时降级为 preface 并入结果。
        hint = self._shuffle_hint() if len(positions) > 1 else None
        preface = ""
        if hint:
            try:
                await event.send(MessageChain([Plain(hint)]))
            except Exception as e:
                logger.warning(f"洗牌提示独立发送失败，降级并入最终结果: {e}")
                preface = hint
        clean = clean_tool_question(question)  # 送 AI 的问题：剥祈使词+阵名（spreads）
        # 今日牌运卡：海报卡面印池内固定签文（卡面装饰）；聊天「牌灵的话」由 AI 按
        # 人设生成（每日牌运与普通占卜共用：同人同日同牌组当天同句）——AI 失败回退
        # 池内当日句。海报渲染失败或 text_only 时回退普通牌面图（_maybe_render_image
        # 统一管发送模式）
        sig_text = ""
        img = None
        persona_eff = None
        if is_daily and daily_uid and picks:
            persona_eff = resolve_persona(self.tarot.ai_persona)
            # 开关 output.daily_card 只控海报渲染：关掉回到普通牌面图，牌灵的话照常
            if self.daily_card and self.tarot.send_mode != "text_only":
                img = await self.daily.render_daily_card(["今日牌运"], picks, daily_uid)
        if not img:
            img = await self.tarot._maybe_render_image(formation, positions, picks)
        # 牌灵的话：所有占卜路径统一生成（羽签/羽时三刻/羽镜/恋羽十字都有这句
        # 牌灵的开口）——裸句直出，无「牌灵的话：」前缀与引号
        if picks:
            eff = persona_eff if persona_eff is not None else resolve_persona(self.tarot.ai_persona)
            sig_text = await self.daily.spirit_cached(event, daily_uid, picks, clean, eff)
        if is_daily and daily_uid:
            interp = await self.daily.interp_cached(event, daily_uid, formation, positions,
                                                    picks, clean, persona_eff)
        else:
            interp = await self.tarot._ai_interpret(
                event, formation, positions, picks, clean or empty_fallback)
        if sig_text:
            # 牌灵的话随 preface 进结果：AI 全失败回退牌义时，牌灵的话一句不丢
            preface = f"{preface}\n{sig_text}" if preface else sig_text
        epilogue_text = random.choice(RESULT_EPILOGUE) if epilogue else ""
        async for r in self.tarot._deliver(event, interp, img, formation, positions, picks,
                                           fail_note=fail_note, preface=preface):
            yield r
        # 收尾句独立直发，不并入合并转发消息（与洗牌提示同模式：asyncgen 被框架
        # 完整迭代，循环后的副作用照常执行）。工具入口 epilogue=False 不触发，
        # 其收尾由 Agent Loop 的 LLM 回复承担。send 失败仅告警，不追加回结果。
        if epilogue_text:
            try:
                await event.send(MessageChain([Plain(epilogue_text)]))
            except Exception as e:
                logger.warning(f"收尾句独立发送失败: {e}")


    # ---------- 今日固定牌运（实现全在 daily.py，入口层不设转发层） ----------

    def _help(self):
        # 正文在 prompts.HELP_TEXT（文案归文案），入口只拼版本头
        return f"🔮 星羽塔罗 {VERSION}\n{HELP_TEXT}"
