"""星羽塔罗核心：抽牌与牌面呈现（StarTarot）——塔罗的「本体」。

与入口（main.py）分离：命令编排、限流、消息发送留在入口；
选阵/问题清洗在 spreads.py，配置语义在 settings.py，
AI 解读（interpret.py + hardening.py）与结果分发（deliver.py）也已独立——
这里只保留「塔罗本身」：抽什么牌、长什么样、牌义怎么写。

换句话：牌怎么抽、抽到哪张、读什么，都在这；别的事不归它管。
"""
# ruff: noqa: F403, F405  # 星导入是 AstrBot 插件惯例，名字进来自框架，静态分析无从溯源
import asyncio
import logging
import os
import random
import sys

# 插件被动态 __import__ 加载时不在 sys.path 中，需显式加入插件目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from card_render import _schedule_image_cleanup, render_cards
from deliver import Deliverer
from interpret import AiInterpreter
from settings import TarotSettings
from spreads import FORMATIONS
from tarot_data import SUIT_CN, TAROT_CARDS

from astrbot.api.all import *

logger = logging.getLogger(__name__)


class StarTarot:
    def __init__(self, context: Context, config: AstrBotConfig = None):
        self.context = context
        # 配置解析收敛在 settings.TarotSettings：默认值、旧配置迁移都在那一层
        # 复制而非持有：让插件各处直接用 self.xxx 取值，不绕道 st.xxx——读起来省心
        st = TarotSettings(config)
        for _k in ("enable_ai", "segment_size", "send_mode", "shuffle_lines",
                   "disclaimer", "daily_fixed", "ai_timeout", "ai_cooldown",
                   "llm_tool_enabled", "llm_tool_cooldown", "cmd_rate_limit",
                   "daily_count_limit", "ai_provider_id", "ai_max_len"):
            setattr(self, _k, getattr(st, _k))
        # 子组件：AI 解读（interpret.py）与结果分发（deliver.py）
        self.interpreter = AiInterpreter(context, self.enable_ai, self.ai_timeout,
                                         self.ai_cooldown, self.ai_provider_id,
                                         self.ai_max_len)
        self.deliverer = Deliverer(self.segment_size, self.disclaimer,
                                   self.send_mode == "forward")
        # 渲染并发锁（×2）：Pillow 拼图丢线程池执行，见 _maybe_render_image
        self._render_lock = asyncio.Semaphore(2)

    def _draw(self, formation: str) -> tuple[list[str], list[dict]]:
        """从 78 张牌里无放回抽 len(阵位) 张，正逆位各 50%。

        无放回（random.sample）：同一个阵不会抽到两张一样的牌；
        正逆位交给 random.random()——纯随机，没有开关、没得刷。
        想固定运势？那是 daily.py 的事，这里只管一把牌。
        """
        positions = FORMATIONS[formation]
        cards = random.sample(TAROT_CARDS, len(positions))
        # 正逆位纯随机（50/50）：占卜嘛，这样才好玩
        return positions, [{"card": c, "upright": random.random() < 0.5} for c in cards]

    @staticmethod
    def _pick_info(pick: dict) -> tuple[str, str, str, str]:
        """一张牌的展示信息四元组：(花色, 牌名, 正/逆位, 对应牌义)。

        牌是正位就读正位释义、逆位读逆位释义——一张牌两个面孔，
        本羽只递你该看到的那一面。
        """
        # (花色, 牌名, 正/逆位, 对应牌义)
        suit, _, cn, _, up, down = pick["card"]
        return suit, cn, "正位" if pick["upright"] else "逆位", up if pick["upright"] else down

    def _render_text(self, formation: str, positions: list[str], picks: list[dict]) -> str:
        """本地牌义兜底文案：AI 解读拿不到时全靠它撑场面。

        每张牌一行：位置 + 牌名 + 正逆位 + 关键释义（tarot_data 里的双行牌义）。
        格式是纯文本，不花哨——AI 挂了的时候，能读、能懂就是最体面的。
        """
        lines = [f"🔮 牌阵：{formation}", "─" * 28]
        for i, (pos, pick) in enumerate(zip(positions, picks), 1):
            suit, cn, state, meaning = self._pick_info(pick)
            lines.append(f"🃏 第{i}张 ·【{pos}】\n「{cn}」（{SUIT_CN[suit]}）{state}\n   {meaning}")
        return "\n".join(lines)

    def _render_image(self, formation: str, positions: list[str], picks: list[dict]) -> str | None:
        # 返回值必须是非空字符串（文件路径或 base64），否则视为失败回退文字版
        try:
            path = render_cards(positions, picks, formation)
        except Exception as e:
            logger.warning(f"牌面图片渲染失败，回退文字版: {e}")
            return None
        if not isinstance(path, str) or not path.strip():
            logger.warning("牌面图片渲染返回空值，回退文字版")
            return None
        return path

    async def _maybe_render_image(self, formation: str, positions: list[str],
                                  picks: list[dict]) -> str | None:
        """牌面图（output.send_mode）：text_only 不渲染（省资源）、不发送，
        走纯文本 + 内置牌义；plain / forward 均渲染（forward 时必有图）。
        返回 None 与渲染失败意义统一，调用方无需区分。

        渲染异步化：Pillow 拼图（读取 2~3MB 素材 ×N + 缩放/旋转/圆角 + PNG 编码）
        同步执行约 0.3~1 秒，会阻塞整个事件循环（所有会话/插件一起卡），
        所以丢进线程池（asyncio.to_thread；Pillow 的 C 扩展释放 GIL，并行真实有效），
        并用信号量限制并发（×2）：单次渲染内存峰值不小，防多人同时占卜时暴涨。

        清理在 card_render 的图片产生点（渲染成功后）注册而非发送出口：
        任何入口、任何路径（AI 成功/兜底/异常中断）都无需关心删除。"""
        if self.send_mode == "text_only":
            return None
        async with self._render_lock:
            img = await asyncio.to_thread(self._render_image, formation, positions, picks)
        _schedule_image_cleanup(img)
        return img

    async def _ai_interpret(self, event, formation: str, positions: list[str],
                            picks: list[dict], user_input: str) -> str | None:
        """AI 结构化解读（委托 AiInterpreter）；牌面详情在核心侧格式化，
        解读器保持对塔罗数据结构零依赖。"""
        lines = []
        for i, (pos, pick) in enumerate(zip(positions, picks), 1):
            _, cn, state, meaning = self._pick_info(pick)
            lines.append(f"- 第{i}张【{pos}】「{cn}」{state}（{meaning}）\n")
        detail = "".join(lines)
        return await self.interpreter.interpret(event, formation, positions, picks, user_input, detail)

    async def _deliver(self, event, interp: str | None, img: str | None, formation: str,
                       positions: list[str], picks: list[dict], fail_note: str = "",
                       preface: str = ""):
        """结果发送（委托 Deliverer），本地牌义由 _render_text 兜底。

        收尾句不收在这里：命令入口在 _run_reading 独立直发、工具入口由
        LLM 回复承担——本方法只负责结果本体与牌义兜底。
        """
        async for r in self.deliverer.deliver(event, interp, img, formation, positions, picks,
                                              self._render_text, fail_note=fail_note,
                                              preface=preface):
            yield r
