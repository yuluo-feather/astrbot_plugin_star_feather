"""星羽塔罗插件入口：命令注册、三入口编排。其他事都分出去了，这里只管进门。

每个模块负责什么，按域分组一次说清楚——别指望我讲第二遍：

- 配置层：settings.py（全部默认值与旧配置迁移）、config.py（读取原语）、identity.py（事件身份标识）
- 占卜核心：spreads.py（选阵与问题清洗）、tarot_core.py（抽牌与牌义）、tarot_data.py（牌库）、daily.py / dailylines.py（今日固定牌运 / 每日签文池）
- AI 解读：interpret.py（候选链与超时）、hardening.py（Prompt 防护）、prompts.py（提示词与全部文案）
- 输出与兜底：deliver.py（发送编排）、card_render.py / fonts.py（渲染与字体）
- 限流与存储：gating.py / limiter.py（限流闸门与纯逻辑）、kv_utils.py（KV 读写原语，daily/gating 共用）
"""
import asyncio
import os
import random
import re

# 导入模型：插件内一律包内相对导入，目录里不留 sys.path.insert / sys.modules.pop。
# 顶层平铺名会让同一个文件出现两套模块身份（补丁打空、按包前缀重载清不到），pop 循环
# 还会误删同解释器里别人的同名模块。包前缀本来就在手上，相对导入是顺着它走。
# 其余模块按域直接实现导入，入口不转发内部函数。红线：tests/test_import_model.py。
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register

from .card_render import cleanup_stale_images
from .daily import DailyFortune, _is_daily_request
from .gating import LimitGate
from .identity import resolve_sender_uid
from .prompts import (
    FAIL_NOTE_TOOL,
    HELP_TEXT,
    RESULT_EPILOGUE,
    SHUFFLE_LINES,
    TOOL_EPILOGUE,
    resolve_persona,
)
from .spreads import clean_tool_question, select_formation
from .tarot_core import StarTarot

VERSION = "0.8.2"

# 配置新鲜度自证：每次加载在 context 上登记「当前代实例」并打一行 [cfg] LOAD，
# 两个入口进门各打一行 [cfg] ENTRY 读数；入口发现自己不是登记的那个实例就 warning。
# 登记放 context 不放模块全局：热重载会重置模块字典，旧实例读的正是旧模块那份全局，
# 永远对得上、什么都报不出来。配置在 __init__ 一次性快照到实例上，所以「跑在旧实例」
# 必然等于「读到旧值」——这条 warning 就是铁证。
# 所有诊断读写都包 try：诊断可以失效，插件加载与占卜不能因此失败。
# 来龙去脉与被排除的几种解释见 tests/test_config_freshness.py 与 TODO 账本。
_ACTIVE_INSTANCE_ATTR = "_sf_active_plugin_instance"

# 读数里报哪几个开关：挑「面板上一改、用户侧立刻看得见」的四项——
# shuffle_lines 判有没有洗牌句、enable_ai 判发不发 AI 请求、ai_timeout 判降级阈值、
# llm_tool_enabled 判自然语言入口开没开。不塞全量配置：读数要能一眼比对。
_CFG_FIELD_NAMES = ("shuffle_lines", "enable_ai", "ai_timeout", "llm_tool_enabled")

# 收尾句独立直发前的等待窗口（秒）：合并转发卡要在协议端上传图片、拼多消息，落地
# 慢于紧随其后的纯文本，不等就会倒序（2026-10-13 实测命令入口 5/5 倒序）。
# 只有转发模式需要；plain / text_only 与 AI 回退路径同走普通链消息，睡了白让用户等。
# 现场调参：仍倒序就把这个值调大（卡片滞后的真实量级尚未测）。
_EPILOGUE_DELAY_SECONDS = 2.0

# 收尾句后台任务的强引用集合：asyncio 只对任务持弱引用，没人引用时可能被 GC 掉，
# 收尾句就静默丢了。任务结束（含取消/异常）由 done callback 自摘。
_EPILOGUE_TASKS: set = set()


async def _send_epilogue_after_window(event, text: str, delay: float) -> None:
    """等窗口再直发收尾句。异常只告警：收尾句是赠品，不许反噬已经交付的结果。"""
    if delay > 0:
        await asyncio.sleep(delay)
    try:
        await event.send(MessageChain([Plain(text)]))
    except Exception as e:
        logger.warning(f"收尾句独立发送失败: {e}")


def _schedule_epilogue(event, text: str, delay: float) -> bool:
    """把「等窗口 + 直发」放进后台任务，命令处理协程不再为此挂起。

    窗口只买「合并转发卡落地慢」这一件事：结果已提交给平台，收尾句晚几秒到即可。
    冻结 event 引用交给任务持有：AstrBot 的 send 只依赖事件对象与平台通道，不要求
    handler 还活着。只在确实走合并转发的平台上调用（见 deliver.supports_forward）——
    将来把请求级队列平台加进白名单时，这里还要单独判「handler 结束后能不能发」。

    契约：永不抛——调度失败只告警并返回 False，由调用方退回同步直发；协程先 close，
    免得日志里多一条 never awaited。
    """
    coro = _send_epilogue_after_window(event, text, delay)
    try:
        task = asyncio.create_task(coro)
    except Exception as e:
        coro.close()
        logger.warning(f"收尾句后台任务登记失败，改用同步直发: {e}")
        return False
    _EPILOGUE_TASKS.add(task)
    task.add_done_callback(_EPILOGUE_TASKS.discard)
    return True


def _cfg_fingerprint(plugin) -> str:
    """一行配置读数：两个对象 id 取十六进制（够比对，又不至于长得读不下去）+ 四个开关值。

    取字段一律 getattr 兜底：这是诊断行，任何字段缺失都不该让它抛——为一行日志把一次
    占卜一起带走，比不打这行严重得多。
    """
    tarot = getattr(plugin, "tarot", None)
    fields = " ".join(f"{n}={getattr(tarot, n, '?')!r}" for n in _CFG_FIELD_NAMES)
    return f"inst={id(plugin):#x} tarot={id(tarot):#x} {fields}"

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
    """
    return len(text.splitlines()) == 1



def _is_help_request(text: str) -> bool:
    """帮助判定唯一入口：两个分支任一命中即算，命令入口与语料测试共用。

    别在别处复刻这段判定：复刻的那份会在调用方收紧（例如多行闸门）时失联——
    测试继续绿，生产行为却已经变了。

    两处 fullmatch 各有职责，不是同一件事的两遍：_HELP_USAGE_RE 的前置字符类允许
    换行、尾部又没有 \\s*，只有整段匹配才拦得住尾换行；这份等价靠上面的 strip() 撑着，
    拿掉 strip 它就会在带尾换行的输入上退化成 match。别以「看着重复」为由动它们。
    """
    t = (text or "").strip()
    return bool(_HELP_REQUEST_RE.fullmatch(t)
                or (_is_single_line(t) and _HELP_USAGE_RE.fullmatch(t)))

# 牌阵定义见 spreads.py（FORMATIONS）

@register("star_feather", "羽落", "星羽塔罗：78张塔罗牌 AI 占卜与深度解读，幻星集素材渲染牌面图", VERSION)
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
        # clock 传核心那份：每日计数的日期键与今日牌运必须落在同一个「今天」
        self.gate = LimitGate(self, self.tarot.cmd_rate_limit, self.tarot.daily_count_limit,
                              clock=self.tarot.clock)
        # 启动回收放到 initialize 里做（见下）：构造期是同步路径，扫盘/删目录
        # 不该堵住事件循环；传自己的目录路径仍是为了让「绝不删自己」有据可依。

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
        # 启动回收过期残骸（确认过期的实例目录 + 旧布局散图）：scan/rmtree 是
        # 阻塞 IO，丢线程池执行，别让一次启动把整个事件循环按住。传自己的目录
        # 路径是让「绝不删自己」这条豁免有据可依——即便时钟跳变让它看起来过期，
        # 也不动（图可能还在发）。见 card_render.cleanup_stale_images。
        tarot = getattr(self, "tarot", None)
        image_dir = getattr(tarot, "image_dir", None)
        if isinstance(image_dir, str) and image_dir:
            try:
                await asyncio.to_thread(cleanup_stale_images, image_dir)
            except Exception as e:
                # 清扫只是磁盘回收，失败不该把插件加载带走
                logger.warning(f"启动回收过期图片失败（不影响加载）: {e}")
        # 登记本代实例并打一行 [cfg] LOAD：热重载每来一次就多一行，与入口的 [cfg] ENTRY
        # 对照即可看出谁跑在谁身上。这一步跑在 initialize 里，一抛就被框架判加载失败，
        # 所以登记与读数都包 try：Context 哪天不许写，就让机制静默空转。诊断可以失效，
        # 插件加载不能失败（同 card_render._schedule_image_cleanup 的「永不抛」口径）。
        context = getattr(self, "context", None)
        if context is not None:
            try:
                setattr(context, _ACTIVE_INSTANCE_ATTR, self)
            except Exception as e:
                logger.warning(f"[cfg] 实例登记失败（新鲜度自证失效）: {e}")
        try:
            logger.info(f"[cfg] LOAD {_cfg_fingerprint(self)}")
        except Exception as e:
            logger.warning(f"[cfg] LOAD 读数行不可用（诊断失效，不影响加载）: {e}")

    def _note_entry_binding(self, entry: str) -> None:
        """入口进门自报读数；跑在上一代实例上就当场 warning。

        读数行每次都有，告警只在对不上时打一行——不吵，对上了就是铁证。
        契约：本函数永不抛（挡 Exception；取消与中断照穿）。它跑在每一次进门的路上，
        两个入口又都把它放在自己的 try 之外：这里抛一次就带走一次占卜，所以连读数
        求值那一步也在保险丝里。
        """
        try:
            logger.info(f"[cfg] ENTRY {entry} {_cfg_fingerprint(self)}")
            active = getattr(getattr(self, "context", None), _ACTIVE_INSTANCE_ATTR, None)
            if active is not None and active is not self:
                logger.warning(
                    f"[cfg] 陈旧实例接管了本次 {entry}：inst={id(self):#x} [{type(self).__module__}] "
                    f"当前代={id(active):#x} [{type(active).__module__}] —— 本次读到的很可能是上一代配置，"
                    f"与上方 [cfg] LOAD 那行对照即可定罪（两侧模块名不同 ⇒ 两份副本，非两代实例）"
                )
        except Exception as e:
            logger.warning(f"[cfg] ENTRY {entry} 自检失败（诊断失效，不影响本次）: {e}")

    def _shuffle_hint(self) -> str | None:
        """洗牌提示语：output.shuffle_lines 关闭时返回 None，调用方不发。
        哼，仪式感这种东西，想关就关，但本羽默认是开着的。"""
        if not self.tarot.shuffle_lines:
            return None
        return random.choice(SHUFFLE_LINES)

    async def _command_entry(self, event: AstrMessageEvent, text: str, *,
                             err_tpl: str, helpable: bool = False,
                             force_daily: bool = False, fixed_formation: str = "",
                             fail_note: str = "",
                             empty_fallback: str = "（未提具体问题，请作一般运势解读）"):
        """命令入口公共流程（/占卜 与 /单抽 共用）：帮助 → 限流 → 抽牌 → 发送。

        err_tpl 是完整的用户侧友好文案（不含异常细节——异常只进日志，
        服务器路径/内部结构不给用户看），其余差异由参数控制。
        前缀/触发门槛由框架先行把关（CommandFilter 命中才进得来），
        这里不再有前缀检查与提示分支——别再把「必须带 /」的旧逻辑加回来。
        """
        # 本次事件已由插件处理：should_call_llm(True) 的语义是「禁止默认 LLM」
        # （框架按 `and not event.call_llm` 判定，默认值是 False，参数名别读反）。
        # 不用 stop_event()：它在响应阶段清空 result 之后才执行，会塞出空 STOP 结果，
        # 收口/统计类插件会当成「空回复」入账（见 changelogs/v0.4.8.md）。
        self._note_entry_binding("command")
        event.should_call_llm(True)
        try:
            text_norm = (text or "").strip()
            if helpable and _is_help_request(text_norm):
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
                    # 命令入口恒发收尾句（工具入口不发，由 Agent Loop 的 LLM 回复承担）。
                    # 这里刻意写死 True 而不是暴露成 _command_entry 的参数：没有「命令
                    # 入口不发收尾」的场景，暴露只会变成死参数或默认 False 的静默回归。
                    # 红线：tests/test_core.py::TestCommandEntryEpilogueEndToEnd
                    epilogue=True):
                yield r
        except Exception as e:
            logger.error(f"命令占卜失败: {e}", exc_info=True)
            # 异常细节只进日志：err_tpl 是纯友好文案，不把路径/内部结构发给用户
            yield event.plain_result(err_tpl)

    @filter.command("占卜", desc="78张塔罗牌 AI 占卜与深度解读")
    async def divine(self, event: AstrMessageEvent, text: str = ""):
        async for r in self._command_entry(
                event, text,
                err_tpl="哼，这场占卜断了。牌灵今天状态不好，换个时候再来问。",
                helpable=True,
                fail_note="📖 解读：\n（AI 今天闹脾气不肯开口，牌面先给你，自己琢磨~）"):
            yield r

    @filter.command("单抽", desc="抽取一张塔罗牌并解读（默认当天固定）")
    async def single(self, event: AstrMessageEvent, text: str = ""):
        async for r in self._command_entry(
                event, text,
                err_tpl="单抽断线了。牌灵今天不太配合，晚点再试。",
                helpable=True,
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

        仅当用户**明确要求**塔罗占卜/抽牌/看运势时调用，例如「帮我算一卦」「抽张牌看看」
        「占卜一下我和她的感情」「最近运势怎么样」。用户的抱怨、感叹、闲聊（如「今天真倒霉」
        「她是不是不喜欢我」）不属于占卜请求，不得调用。相邻调用若在节流期内，直接说明并结束，
        不重复占卜。

        发送约定：内容一律 event.send 直发，结尾 yield 一个字符串给 Agent Loop 生成收尾回复。
        不能 yield MessageEventResult 或 None —— 否则 runner 判定无返回值并 DONE，respond
        空消息跳过，收口钩子不触发、群聊防并发门闩死锁（见 _tool_send）。
        """
        self._note_entry_binding("tool")
        # 打开开关：运行期判断（装饰器静态注册无法卸载，见 __init__ 注释）
        if not self.tarot.llm_tool_enabled:
            await self._tool_send(event, "星羽塔罗的自然语言占卜未开启～ 试试 /占卜 感情 这样的命令。")
            # 非占卜分支：不 yield 收尾池文案（它说「占卜结果已发送」，这里没有结果），
            # 改为明确引导，让 LLM 自然回一句即可（仍是字符串，保 Agent Loop 不断）
            yield "已告知用户自然语言占卜未开启。请自然回应一句，不必再调用工具。"
            return
        # message_str 属性异常/缺失时按空文本处理（保 Agent Loop 不中断）
        text = (getattr(event, "message_str", "") or "").strip()
        # 帮助请求不占卜：模型偶尔会把「占卜怎么用」也调到这里，
        # 命中帮助页就直发并结束，别真抽一副牌
        if _is_help_request(text):
            await self._tool_send(event, self._help())
            yield "帮助页已发送。请自然回应一句，不必再调用工具。"
            return
        if not text:
            yield "工具收到空文本：用户没说占卜什么。请回一句，引导用户说出想占卜的内容（如感情、事业、学业、今日运势）。"
            return
        # 工具节流：同会话冷却期内不重复触发，防止模型连续调用刷屏；
        # umo 缺失回退 "global"（与 gating.check 命令入口口径一致，避免拼出 sf_tool_cd_None）
        umo = (getattr(event, "unified_msg_origin", None) or "global")
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
        # 收尾引导语（prompts 集中管理，随机一条）：让 LLM 生成一句简短确认（同时保住 respond 非空）。
        # 放在四条早退分支之后取：早退路径（开关关闭/空文本/节流/每日上限）用不到 note，
        # 提前抽只会白耗一次全局随机状态（也把「不走的路径别做无用功」钉住）。
        # 红线：tests/test_core.py::TestDivineToolDirect（早退不消耗随机 + 正常路径必消耗）
        note = random.choice(TOOL_EPILOGUE)
        try:
            # 统一抽牌 + 统一流程（与 /占卜 同一套逻辑，行为一致）
            is_daily, daily_uid, formation, positions, picks = await self._pick_reading(event, text)
            async for r in self._run_reading(event, formation, positions, picks, text,
                                             is_daily=is_daily, daily_uid=daily_uid,
                                             fail_note=FAIL_NOTE_TOOL):
                # 直发原结果对象而非拆包重装：它是 MessageChain 的子类（另带 use_t2i_ /
                # result_content_type），拆出 chain 再套一层会把这些字段静默丢回默认值；
                # 日后 _run_reading 换产出形态也不会有「探不到 chain 就悄悄不发」的哑路径。
                await event.send(r)
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

        - 运势类请求（运势/运气/牌运等词，或 /单抽 的 force_daily）且 daily_fixed 开启
          → 今日固定牌运（同一天同一人同一张牌）；
        - 具体问题、开关关闭或固定不可用 → 自由随机；随机时 fixed_formation 可锁定阵型
          （/单抽 固定羽签），否则按问题选阵。

        返回 (is_daily, daily_uid, formation, positions, picks)；is_daily=True 时解读走
        当日缓存，False 走 AI 现场解读。
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

    async def _ensure_image(self, img: str | None, formation: str,
                            positions: list[str], picks: list[dict]) -> str | None:
        """发送前核验牌面图：文件不在或为空就补渲染一次，再失败就降级为无图。

        只判「这一秒拿到的图存在且非空」，不判图片年龄——渲染已经排到两段 AI 之后、
        距平台读文件只剩毫秒，这里守的是出口纪律：图片问题绝不带走整条消息
        （Issue #2 的教训；清理定时器只当保险丝，见 card_render.IMAGE_TTL_SECONDS）。
        """
        if not isinstance(img, str) or not img:
            return img  # 本就无图（text_only / 渲染失败）：无需核验，别白跑一次渲染
        try:
            # getsize 一次调用同时判「在不在」与「是不是空」：分成 exists + getsize
            # 两步时，文件可能正好在两步之间被删掉。
            size = os.path.getsize(img)
        except (OSError, ValueError) as e:
            # 删除、权限、路径非法（含 NUL）一律当缺失：问不出「非空文件」就交给补渲染，
            # 把未核验的路径交给发送侧只会让整条消息被打回。
            size, why = 0, f"核验失败：{e}"
        else:
            why = "是空文件"
        if size > 0:
            return img
        logger.warning(f"牌面图在发送前不可用（{why}）：{img}")
        try:
            new_img = await self.tarot._maybe_render_image(formation, positions, picks)
        except Exception as e:
            # 渲染器自己吞异常，这里是第二道：补渲染再炸一次就会把整条消息也带走。
            # 两处「永不抛」相叠是有意的——内层守渲染实现，这里守「图片问题绝不带走消息」。
            logger.warning(f"重渲染牌面图异常，本签降级为无图: {e}")
            return None
        if not new_img:
            logger.warning("重渲染仍失败，本签降级为无图（解读附带逐牌牌义）")
        return new_img

    async def _run_reading(self, event: AstrMessageEvent, formation: str, positions: list[str],
                           picks: list[dict], question: str, is_daily: bool = False,
                           daily_uid: str = "", fail_note: str = "",
                           empty_fallback: str = "（未提具体问题，请作一般运势解读）",
                           epilogue: bool = False):
        """统一占卜流程：洗牌提示 → 两段 AI（牌灵的话 + 解读，并发）→ 渲染牌面 → 分段发送。

        两段 AI 共用同一笔总预算（AiBudget）；解读来源：今日固定且已知用户时走当天
        解读缓存，否则 AI 现场解读（失败由 _deliver 回退牌义）。
        洗牌提示、发送模式等开关只在本函数生效，入口无需感知；单张牌阵不发洗牌提示。
        """
        # 洗牌提示不走 yield，用 event.send 独立发出：命令入口每个 yield 都会各发一次，
        # 工具入口的 Agent Loop 只取最后一个；直发在两边语义一致。send 失败并入结果。
        hint = self._shuffle_hint() if len(positions) > 1 else None
        preface = ""
        if hint:
            try:
                await event.send(MessageChain([Plain(hint)]))
            except Exception as e:
                logger.warning(f"洗牌提示独立发送失败，降级并入最终结果: {e}")
                preface = hint
        clean = clean_tool_question(question)  # 送 AI 的问题：剥祈使词+阵名（spreads）
        sig_text = ""
        # 一次占卜只掷一次骰子：牌灵的话与解读正文必须是同一人格（random 各自解析会
        # 出现「牌灵傲娇、正文温柔」两副腔调），这里解析一次，全路径共用。
        persona_eff = resolve_persona(self.tarot.ai_persona)
        # 一次占卜一笔 AI 总预算（ai.ai_budget_seconds）：候选重试与供应商回退都不重置
        # 截止点；它只管 AI 阶段，渲染在预算窗口之外。
        budget = self.tarot.new_budget()
        # 两段 AI 没有数据依赖、共用同一个**绝对**截止点：并发后耗时 ≈ max(两段)，
        # 串行等于白等一个 LLM 往返。失败语义不变，任一段抛异常仍旧冒泡到入口兜底。
        spirit_call = (self.daily.spirit_cached(event, daily_uid, picks, clean, persona_eff,
                                                budget)
                       if picks else None)
        if is_daily and daily_uid:
            interp_call = self.daily.interp_cached(event, daily_uid, formation, positions,
                                                   picks, clean, persona_eff, budget)
        else:
            interp_call = self.tarot._ai_interpret(
                event, formation, positions, picks, clean or empty_fallback,
                persona_eff=persona_eff, budget=budget)
        if spirit_call is None:
            sig_text, interp = "", await interp_call
        else:
            sig_text, interp = await asyncio.gather(spirit_call, interp_call)
        if sig_text:
            # 牌灵的话随 preface 进结果：AI 全失败回退牌义时，牌灵的话一句不丢
            preface = f"{preface}\n{sig_text}" if preface else sig_text
        # 图片后置渲染：排在两段 AI 之后、发送之前，渲染点到平台读文件只剩毫秒。
        # 旧顺序下图要在整段 AI 等待里存活，清理保险丝到点就删、平台读到空文件会把
        # 整条消息打回（Issue #2）。代价是渲染耗时（0.3~2.5s）落在等待末尾，但洗牌
        # 提示早已发出，这段不是静默的；渲染不吃 AI 预算。
        img = None
        if is_daily and daily_uid and picks:
            # 今日牌运卡：海报印池内固定签文，聊天那句由 AI 按人设生成（同人同日同牌组
            # 当天同句）。output.daily_card 只控海报：关掉/渲染失败/text_only 都回退普通牌面图。
            if self.daily_card and self.tarot.send_mode != "text_only":
                img = await self.daily.render_daily_card(["今日牌运"], picks, daily_uid)
        if not img:
            img = await self.tarot._maybe_render_image(formation, positions, picks)
        # 发送前核验牌面图：② 之后它只剩一种情形要管——渲染明明成功、文件却在这几毫秒
        # 里被外部删掉（磁盘清理、别的进程、人工 rm）。不再是主力兜底，但也不删：
        # 它守的是「图片问题绝不带走消息」这条出口纪律，与渲染顺序无关。
        img = await self._ensure_image(img, formation, positions, picks)
        epilogue_text = random.choice(RESULT_EPILOGUE) if epilogue else ""
        async for r in self.tarot._deliver(event, interp, img, formation, positions, picks,
                                           fail_note=fail_note, preface=preface):
            yield r
        # 收尾句独立直发，不并入合并转发（工具入口 epilogue=False，收尾交给 Agent Loop）；
        # send 失败仅告警。转发卡落地慢于紧随其后的纯文本，所以先等一个窗口——窗口放在
        # 后台任务里等，handler 立刻收尾，用户不为一句收尾多等两秒。
        if epilogue_text:
            # 只有合并转发需要等：plain / text_only 与 AI 回退路径都是普通链消息，
            # 提交次序即落地次序。用 forward_for 而不是裸配置：平台收不下 Nodes 时
            # （WebChat）实际走普通链消息，不该再等；后台任务在那边也不适用
            # （handler 结束后请求队列会被移除）。
            forwarded = bool(interp) and self.tarot.deliverer.forward_for(event)
            scheduled = (forwarded and _EPILOGUE_DELAY_SECONDS > 0
                         and _schedule_epilogue(event, epilogue_text, _EPILOGUE_DELAY_SECONDS))
            if not scheduled:
                try:
                    await event.send(MessageChain([Plain(epilogue_text)]))
                except Exception as e:
                    logger.warning(f"收尾句独立发送失败: {e}")


    # ---------- 今日固定牌运（实现全在 daily.py，入口层不设转发层） ----------

    def _help(self):
        # 正文在 prompts.HELP_TEXT（文案归文案），入口只拼版本头
        return f"🔮 星羽塔罗 {VERSION}\n{HELP_TEXT}"
