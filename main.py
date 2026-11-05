"""星羽塔罗插件入口：只注册命令/工具，把请求转给 app.handlers。

模块说明：
- 装配：app/container.py（唯一装配处）
- 入口：app/handlers.py（命令 / 工具）
- 纯逻辑：core/（不 import astrbot；日志走 core/log.py 的中立门面）
- 编排：services/（占卜流水线、渲染）
- 适配：adapters/（KV、消息、呈现）
"""
import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .adapters.astrbot_io import messenger_for
from .adapters.event import delivery_capabilities, incoming_from_event, mark_handled
from .adapters.log import bind_host_logger
from .app import handlers
from .app.container import build_container
from .app.diagnostics import image_dir_of, note_entry_binding, register_instance
from .app.webui import register_web_apis
from .render.assets import asset_manifest
from .render.store import cleanup_stale_images

VERSION = "0.10.0"


@register("star_feather", "羽落", "星羽塔罗：78张塔罗牌 AI 占卜与深度解读，幻星集素材渲染牌面图", VERSION)
class StarFeatherPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        bind_host_logger()  # 核心层的中立日志出口从这里接上宿主；失败只降级
        self.container = build_container(context, self, config)
        # 可视化视图（views/）的只读接口：老版本宿主没有 register_web_api 时
        # 只是少一个入口，插件加载照旧。
        register_web_apis(context, self.container, VERSION)

    async def initialize(self):
        """实例化后的异步初始化钩子：启动横幅、过期图片回收、配置新鲜度登记。"""
        logger.info(fr"""
 ____  _               _____          _   _
/ ___|| |_ __ _ _ __  |  ___|__  __ _| |_| |__   ___ _ __
\___ \| __/ _` | '__| | |_ / _ \/ _` | __| '_ \ / _ \ '__|
 ___) | || (_| | |    |  _|  __/ (_| | |_| | | |  __/ |
|____/ \__\__,_|_|    |_|  \___|\__,_|\__|_| |_|\___|_|
星羽塔罗 v{VERSION} · 78张牌已就位，牌灵在等你来问。
""")
        # 素材清单是牌面渲染的入口：清单为空说明打包缺文件。导入不会报错，
        # 只有第一次渲染才 FileNotFoundError，而渲染兜底会把它吞成纯文字——
        # 启动时先喊一声，别让用户以为「牌面图功能没了」。
        try:
            if not asset_manifest():
                logger.error(
                    "资产清单为空：assets/manifest.json 缺失或损坏，"
                    "牌面渲染将全部降级为文字；请检查插件包完整性并重装。")
        except Exception as e:
            logger.error(f"资产清单检查失败（牌面渲染可能降级为文字）: {e}")
        image_dir = image_dir_of(self)
        if image_dir:
            try:
                await asyncio.to_thread(cleanup_stale_images, image_dir)
            except Exception as e:
                logger.warning(f"启动回收过期图片失败（不影响加载）: {e}")
        # 登记当前代并打一行 [cfg] LOAD：诊断可以失效，插件加载不能失败。
        register_instance(self)

    def _help(self) -> str:
        return handlers.help_text(VERSION)

    def _entry_parts(self, event):
        """宿主侧收口：原始事件只在这里转成 DTO / 投递能力 / 消息端口。"""
        caps = delivery_capabilities(
            event, self.container.settings.output.send_mode == "forward")
        return incoming_from_event(event), caps, messenger_for(event)

    async def _run_command(self, event, text: str, **kwargs) -> None:
        note_entry_binding(self, "command")
        mark_handled(event)
        incoming, caps, messenger = self._entry_parts(event)
        await handlers.command_entry(
            self.container, incoming, caps, messenger, text, **kwargs)

    @filter.command("占卜", desc="78张塔罗牌 AI 占卜与深度解读")
    async def divine(self, event: AstrMessageEvent, text: str = ""):
        await self._run_command(
            event, text, version=VERSION,
            err_tpl="哼，这场占卜断了。牌灵今天状态不好，换个时候再来问。",
            fail_note="📖 解读：\n（AI 今天闹脾气不肯开口，牌面先给你，自己琢磨~）")

    @filter.command("单抽", desc="快速抽一张牌（每次现抽，不按天固定）")
    async def single(self, event: AstrMessageEvent, text: str = ""):
        await self._run_command(
            event, text, version=VERSION,
            err_tpl="单抽断线了。牌灵今天不太配合，晚点再试。",
            intent_kind="single")

    @filter.command("今日牌运", alias={"运势", "每日一签"},
                    desc="今天的固定牌运（同一天同一人始终同一张牌）")
    async def daily(self, event: AstrMessageEvent, text: str = ""):
        await self._run_command(
            event, text, version=VERSION,
            err_tpl="今日牌运断线了。牌灵今天不太配合，晚点再试。",
            intent_kind="daily", empty_fallback="（今日牌运）")

    @filter.llm_tool(name="star_feather_divine")
    async def divine_tool(self, event: AstrMessageEvent, formation: str = ""):
        """星羽塔罗占卜：78 张塔罗牌抽牌，输出牌面图与解读。

        仅当用户**明确要求**塔罗占卜/抽牌/看运势时调用，例如「帮我算一卦」「抽张牌看看」
        「占卜一下我和她的感情」「最近运势怎么样」。用户的抱怨、感叹、闲聊（如「今天真倒霉」
        「她是不是不喜欢我」）不属于占卜请求，不得调用。相邻调用若在节流期内，直接说明并结束，
        不重复占卜。

        发送约定：内容一律 event.send 直发，结尾 yield 一个字符串给 Agent Loop 生成收尾回复。
        不能 yield MessageEventResult 或 None —— 否则 runner 判定无返回值并 DONE，respond
        空消息跳过，收口钩子不触发、群聊防并发门闩死锁。

        Args:
            formation(string): 牌阵名，只能是这四个之一：羽签（单张问询）、羽时三刻（时间之流）、
                羽镜（圣三角）、恋羽十字（恋人十字）。用户明确提到牌阵、或问题重心清楚时填写；
                拿不准就留空，插件会按问题规则自动选择。
        """
        note_entry_binding(self, "tool")
        incoming, caps, messenger = self._entry_parts(event)
        yield await handlers.tool_entry(
            self.container, incoming, caps, messenger, formation, version=VERSION)
