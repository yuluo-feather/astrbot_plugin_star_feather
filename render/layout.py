"""纯几何：画布尺寸、卡牌网格与坐标，不依赖 Pillow。"""
from dataclasses import dataclass

# ---------- 配色 ----------
CAPSULE_TEXT = (248, 249, 252)
LINE = (130, 140, 170)
CAPSULE_BG = (42, 54, 84)
RED = (190, 90, 90)
BAR_TITLE = (170, 128, 42)
BAR_TEXT = (70, 84, 100)
CARD_BORDER = (200, 176, 120)

CARD_OUT_W = 340
PAD_X = 18
INNER_W = CARD_OUT_W - PAD_X * 2
INNER_H = 608
INFO_H = 104
CARD_H = PAD_X * 2 + INNER_H + INFO_H

LABEL_H = 70
PAD = 36
GAP = 28
TITLE_H = 96

DAILY_CARD_W = 680
DAILY_CARD_H = 1200
DAILY_CARD_Y = 176
DAILY_QUOTE_GAP = 68
DAILY_CARD_QUOTE_Y = DAILY_CARD_Y + CARD_H + DAILY_QUOTE_GAP
DAILY_SIG_TEXT = (236, 227, 196)
DAILY_DATE_TEXT = (208, 214, 226)
DAILY_SIGN_TEXT = (186, 194, 208)
DAILY_FOOT_TEXT = (236, 188, 204)


@dataclass(frozen=True)
class CardGrid:
    """一次多张牌阵的画布与网格。"""

    cols: int
    rows: int
    width: int
    height: int

    def origin(self, index: int) -> tuple[float, float]:
        col = index % self.cols
        row = index // self.cols
        x = PAD + col * (CARD_OUT_W + GAP)
        y = TITLE_H + row * (LABEL_H + CARD_H + GAP)
        return x, y


def card_grid(n: int) -> CardGrid:
    """n 张牌的排列：1 张单列、4 张 2×2、其余单行。"""
    cols = 1 if n <= 1 else (2 if n == 4 else n)
    rows = (n + cols - 1) // cols
    width = cols * CARD_OUT_W + (cols - 1) * GAP + PAD * 2
    height = TITLE_H + rows * (LABEL_H + CARD_H) + (rows - 1) * GAP + PAD * 2
    return CardGrid(cols, rows, width, height)
