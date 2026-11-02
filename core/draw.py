"""抽牌与牌面文本：纯函数，不依赖框架、不碰存储与 AI。

塔罗的「本体」只剩这些：抽什么牌、每张牌展示什么、无 AI 时怎么把牌义写成文字。
渲染、发送、缓存都不在这里。
"""
import random as _random

from ..spreads import FORMATIONS
from ..tarot_data import TAROT_CARDS, meaning_text
from .models import Pick
from .ports import Rng


def draw(formation: str, rng: Rng | None = None) -> tuple[list[str], list[Pick]]:
    """从 78 张牌里无放回抽 len(阵位) 张，正逆位各 50%。

    Args:
        formation: 牌阵名，必须在 spreads.FORMATIONS 里。
        rng: 随机源，默认全局 random；测试可注入固定种子。

    Returns:
        (牌位名列表, 抽中的牌列表)。
    """
    positions = list(FORMATIONS[formation])
    source = rng if rng is not None else _random
    cards = source.sample(TAROT_CARDS, len(positions))
    return positions, [Pick(card=card, upright=source.random() < 0.5) for card in cards]


def pick_info(pick: Pick) -> tuple[str, str, str, str]:
    """一张牌的展示信息四元组：(花色, 牌名, 正/逆位, 对应牌义)。"""
    card = pick["card"]
    upright = bool(pick["upright"])
    return (card["suit"], card["cn"], "正位" if upright else "逆位",
            meaning_text(card, upright))


def render_text(formation: str, positions: list[str], picks: list[Pick]) -> str:
    """本地牌义兜底文案：AI 解读拿不到时全靠它撑场面。"""
    lines = [f"🔮 牌阵：{formation}", "─" * 28]
    for i, (pos, pick) in enumerate(zip(positions, picks), 1):
        _, cn, state, meaning = pick_info(pick)
        # 牌名已自带花色，别再补「（花色）」——红线 test_core 会拦。
        lines.append(f"🃏 第{i}张 ·【{pos}】\n「{cn}」{state}\n   {meaning}")
    return "\n".join(lines)


def render_detail(positions: list[str], picks: list[Pick]) -> str:
    """送 AI 的牌面详情：每张一行，解读器对塔罗数据结构零依赖。"""
    lines = []
    for i, (pos, pick) in enumerate(zip(positions, picks), 1):
        _, cn, state, meaning = pick_info(pick)
        lines.append(f"- 第{i}张【{pos}】「{cn}」{state}（{meaning}）\n")
    return "".join(lines)
