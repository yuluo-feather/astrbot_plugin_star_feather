import asyncio
import logging
import os
import random
import re
import sys

# 插件被动态 __import__ 加载时不在 sys.path 中，需显式加入插件目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from card_render import render_cards
from tarot_data import SUIT_CN, TAROT_CARDS

from astrbot.api.all import *
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Image, Node, Nodes, Plain

logger = logging.getLogger(__name__)

VERSION = "0.4.6"

# 常量：集中管理散落的默认值 / 间隔
DEFAULT_SEGMENT_SIZE = 300   # 无标记文本兜底分段长度
MIN_SEGMENT_SIZE = 50        # 分段长度下限
SEND_INTERVAL = 0.3          # 普通模式逐条发送间隔（秒）

# 洗牌提示随机池：小羽毛口吻，每次占卜随机出现一句
SHUFFLE_LINES = [
    "✨ 洗牌中……牌灵听见你的问题了，别催。",
    "✨ 洗牌中……牌灵正在读你的问题，它看得可认真了。",
    "✨ 洗牌中……不许催，牌要一张张洗才有灵性。",
    "✨ 洗牌中……牌灵说，这个问题有点意思。",
    "✨ 洗牌中……牌灵在挑牌，要挑一张最配你的。",
    "✨ 洗牌中……别急，牌灵正在找那几张有缘的。",
]

# 牌阵定义：牌位列表（张数取 len）
FORMATIONS = {
    "羽签": ["你的当下"],
    "羽时三刻": ["过去", "现在", "未来"],
    "羽镜": ["现状", "阻碍", "建议"],
    "恋羽十字": ["你", "对方", "关系现状", "未来走向"],
}

# 语义关键词 → 牌阵（权重累计，阵名词见 FORMATION_ALIASES）
KEYWORD_MAP = [
    (("情感", "爱情", "恋爱", "喜欢", "感情", "分手", "复合"), "恋羽十字"),
    (("事业", "工作", "面试", "学业", "考试", "考研", "升职"), "羽镜"),
    (("过去", "未来", "时间线", "运势"), "羽时三刻"),
]

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


class StarTarot:
    def __init__(self, context: Context, config: AstrBotConfig = None):
        self.context = context
        get = (lambda k, d: config.get(k, d)) if config else (lambda k, d: d)
        self.enable_ai = get("enable_ai", True)
        self.forward_result = get("forward_result", False)
        # 显式兜底：未设置 / None / 非数字一律回默认，意图清晰
        raw_size = get("segment_size", DEFAULT_SEGMENT_SIZE)
        try:
            self.segment_size = max(MIN_SEGMENT_SIZE, int(raw_size))
        except (TypeError, ValueError):
            self.segment_size = DEFAULT_SEGMENT_SIZE

    def _select_formation(self, text: str) -> str:
        # 三层决策：显式指定阵名 > 关键词权重累计 > 内容推断 > 兜底
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
            return best_name
        if any(k in text for k in ("他", "她", "我们", "喜欢我吗", "还爱")):
            return "恋羽十字"
        return "羽时三刻"

    def _strip_alias(self, text: str) -> str:
        # 剥离「作为独立词出现」的阵名（前后为空白/标点/边界），避免干扰 AI 语义；
        # 句中动词用法如「单抽一张牌」不受影响（选阵层保留宽松匹配，此处才收紧）
        text = text or ""
        for pat, _ in _ALIAS_PATTERNS:
            text = pat.sub("", text)
        return re.sub(r"[ 	]{2,}", " ", text).strip(" 	，。、！？：；,.!?;:")

    @staticmethod
    def _pick_info(pick: dict) -> tuple[str, str, str, str]:
        # (花色, 牌名, 正/逆位, 对应牌义)
        suit, _, cn, _, up, down = pick["card"]
        return suit, cn, "正位" if pick["upright"] else "逆位", up if pick["upright"] else down

    def _draw(self, formation: str) -> tuple[list[str], list[dict]]:
        positions = FORMATIONS[formation]
        cards = random.sample(TAROT_CARDS, len(positions))
        return positions, [{"card": c, "upright": random.random() < 0.5} for c in cards]

    def _render_text(self, formation: str, positions: list[str], picks: list[dict]) -> str:
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

    def _require_prefix(self, event: AstrMessageEvent) -> bool:
        # 触发规则与文档一致：私聊必须带 / 前缀；群聊必须 @ 机器人
        # 判定与框架 waking_check 对齐：At 组件命中自身 QQ；@全体成员同样放行
        raw = (getattr(event.message_obj, "message_str", "") or "").strip()
        if raw.startswith("/"):
            return True
        if event.is_private_chat():
            return False
        self_id = str(event.get_self_id()) if hasattr(event, "get_self_id") else ""
        msg = event.get_messages() if hasattr(event, "get_messages") else (getattr(event.message_obj, "message", None) or [])
        for c in msg:
            # @全体成员：NapCat 等解析为 At(qq="all")，AtAll 为其子类（qq="all"），统一按 qq 判定
            if isinstance(c, At) and (str(c.qq) == "all" or (self_id and str(c.qq) == self_id)):
                return True
        if not self_id:  # 拿不到自身 ID 时降级：仅认 At 组件存在
            return any(isinstance(c, At) for c in msg)
        return False

    async def _ai_interpret(self, formation: str, positions: list[str], picks: list[dict], user_input: str) -> str | None:
        """AI 结构化解读，失败返回 None 由调用方兜底。"""
        if not self.enable_ai:
            return None
        detail = "".join(
            f"- 第{i}张【{pos}】「{cn}」{state}（{meaning}）\n"
            for i, (pos, pick) in enumerate(zip(positions, picks), 1)
            for suit, cn, state, meaning in [self._pick_info(pick)]
        )
        prompt = (
            f"你是一位温柔而专业的塔罗占卜师。用户的问题是：“{user_input}”。\n"
            f"牌阵：{formation}\n抽牌结果：\n{detail}\n"
            "请按以下格式输出解读：每张牌单独成段，段落开头用【第N张·位置】这样的标记"
            "（例如【第1张·过去】），每段 80~140 字，内容贴合牌位与问题；"
            "最后单独输出一段【总结】，用直白的话一句话说清整体结果与建议（50字左右）。"
            "不要重复用户问题，不要使用 Markdown 表格。格式示例：\n"
            "【第1张·过去】……（本段解读）\n【第2张·现在】……\n【第3张·未来】……\n【总结】……（一句话结论）"
        )
        try:
            resp = await self.context.get_using_provider().text_chat(
                prompt=prompt, session_id=None, contexts=[], image_urls=[],
                system_prompt="你是专业塔罗占卜师，语言温柔、精准、有画面感。")
            return resp.completion_text.strip()
        except Exception as e:
            logger.warning(f"AI 解读失败，回退本地牌义: {e}")
            return None

    def _split_text(self, text: str, size: int) -> list[str]:
        # 无标记文本按长度切分：优先在换行处断开，拼接可还原原文
        text = (text or "").strip()
        if not text:
            return []
        parts = []
        while len(text) > size:
            cut = text[:size].rfind("\n")
            if cut <= 0:  # 无换行或换行在最前：硬切
                parts.append(text[:size])
                text = text[size:]
            else:  # 在换行处断开（含换行符），保证拼接可还原
                parts.append(text[: cut + 1])
                text = text[cut + 1:]
        return parts + [text]

    def _split_sections(self, text: str) -> list[str]:
        # 按【第N张·位置】/【总结】标记切分为结构化段落（不依赖换行）；无标记退回长度切分
        text = (text or "").strip()
        if not text:
            return []
        segs = re.split(r"(?=【(?:第\d+张[^】]*|总结)】)", text)
        if len(segs) < 3:  # segs[0] 为标记前前言；不足两个标记段则按长度兜底
            return self._split_text(text, self.segment_size)
        return [seg.strip() for seg in segs[1:] if seg.strip()]

    async def _deliver(self, event: AstrMessageEvent, interp: str | None, img: str | None, formation: str,
                       positions: list[str], picks: list[dict], fail_note: str = ""):
        # 统一发送占卜结果：AI 成功 → 结构化发送；失败 → 图片/文字兜底
        if not interp:
            yield event.image_result(img) if img else event.plain_result(self._render_text(formation, positions, picks))
            if fail_note:
                yield event.plain_result(fail_note)
            return
        # 结构化切分 + 按 segment_size 二次截断（模型可能无视 80~140 字限制），防单段超长
        parts = [sub for seg in self._split_sections(interp) for sub in self._split_text(seg, self.segment_size)]
        if not parts:
            return
        # 转发节点 uin：取机器人自身 QQ，取不到时用 '0'
        raw = getattr(event.message_obj, "raw_message", None)
        uin = str(raw["self_id"]) if isinstance(raw, dict) and raw.get("self_id") else "0"
        if self.forward_result:
            nodes = ([Node(content=[Image(file=img), Plain("\n✨ 星羽塔罗 · 牌面")], name="星羽塔罗", uin=uin)] if img else []) + \
                    [Node(content=[Plain(seg)], name="星羽塔罗", uin=uin) for seg in parts]
            yield event.chain_result([Nodes(nodes)])
        else:
            if img:
                yield event.image_result(img)
            for seg in parts:
                yield event.plain_result(seg)
                await asyncio.sleep(SEND_INTERVAL)


@register("star_feather", "羽落", "星羽塔罗：78张塔罗牌 AI 占卜与深度解读，官方素材渲染牌面图", "0.4.6")
class StarFeatherPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.tarot = StarTarot(context, config)

    @command("占卜")
    async def divine(self, event: AstrMessageEvent, text: str = ""):
        if not self.tarot._require_prefix(event):
            yield event.plain_result("提示：触发占卜请带 / 前缀，例如「/占卜 我和她感情如何」")
            event.stop_event()
            return
        try:
            if "帮助" in text or "help" in text.lower():
                yield event.plain_result(self._help())
                return
            formation = self.tarot._select_formation(text)
            clean = self.tarot._strip_alias(text)  # 阵名剥离后再送 AI，语义更纯净
            positions, picks = self.tarot._draw(formation)
            yield event.plain_result(random.choice(SHUFFLE_LINES))
            await asyncio.sleep(1)
            img = self.tarot._render_image(formation, positions, picks)
            interp = await self.tarot._ai_interpret(
                formation, positions, picks, clean or "（未提具体问题，请作一般运势解读）")
            async for r in self.tarot._deliver(
                    event, interp, img, formation, positions, picks,
                    fail_note="📖 解读：\n（AI 今天闹脾气不肯开口，牌义先给你，自己琢磨~）"):
                yield r
            event.stop_event()
        except Exception as e:
            logger.error(f"占卜失败: {e}")
            yield event.plain_result(f"哼，这场占卜断了：{e}。牌灵今天状态不好，换个时候再来问。")

    @command("单抽")
    async def single(self, event: AstrMessageEvent, text: str = ""):
        if not self.tarot._require_prefix(event):
            yield event.plain_result("提示：触发单抽请带 / 前缀，例如「/单抽」")
            event.stop_event()
            return
        try:
            positions, picks = self.tarot._draw("羽签")
            img = self.tarot._render_image("羽签", positions, picks)
            interp = await self.tarot._ai_interpret("羽签", positions, picks,
                                                      self.tarot._strip_alias(text) or "（今日运势）")
            async for r in self.tarot._deliver(event, interp, img, "羽签", positions, picks):
                yield r
            event.stop_event()
        except Exception as e:
            logger.error(f"单抽失败: {e}")
            yield event.plain_result(f"单抽断线了：{e}。牌灵今天不太配合，晚点再试。")

    def _help(self):
        return (f"🔮 星羽塔罗 {VERSION}\n「/占卜 [问题]」- 智能选牌阵，可带关键词（情感/事业/运势）\n"
                "「/单抽」- 快速抽一张，今日牌运\n牌阵：羽签 / 羽时三刻 / 羽镜 / 恋羽十字（经典名 单张问询·时间之流·圣三角·恋人十字 也认）\n"
                "显式指定：/占卜 圣三角 考研如何（新名/经典名均可）\n"
                "需带 / 前缀触发；群聊中 @ 机器人也可触发。\n78 张牌官方素材渲染牌面图，AI 解读失败自动回退牌义。")
