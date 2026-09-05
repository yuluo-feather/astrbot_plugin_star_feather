"""星羽塔罗 - 牌面图片渲染模块

使用 B 站幻星集主题塔罗牌素材（assets/）渲染牌面：
- 所有牌统一「白边卡牌」样式：外层白色圆角卡底 + 内部牌面图，视觉整齐
- 画布背景为本签随机一张牌面放大做底 + 深藏青遮罩（与今日牌运卡同款风味）
- 逆位时内部牌面图旋转 180°，信息区标注正逆位 + 牌名 + 牌义关键词
素材缺失时抛出异常，由上层（tarot_core）回退纯文本牌面。

——本羽最得意的部分：牌要出得好看，才配得上占卜。
"""
import asyncio
import logging
import os
import random
import secrets
import tempfile
import time

from PIL import Image, ImageDraw

from fonts import _load_font
from tarot_data import SUIT_CN

logger = logging.getLogger(__name__)


# ---------- 产物生命周期：渲染出的临时图片由本模块统一管理 ----------
async def _delayed_remove(path: str, delay: int = 30) -> None:
    """延迟删除牌面图：框架取图发送要几秒，立刻删会发出坏图；
    等 delay 秒再删，既不耽误发送，也防临时目录无限堆积。"""
    await asyncio.sleep(delay)
    try:
        os.remove(path)
    except OSError:
        pass  # 已被启动清理/其他路径处理，无需管


def _schedule_image_cleanup(img: str, delay: int = 30) -> None:
    """图片产生点即绑定清理（而不是绑定发送出口）：无论后续走 AI 成功、
    文字兜底还是异常中断，任务都已在渲染成功后注册完毕；
    不在事件循环（如测试）或进程被杀时，由启动清理兜底。
    delay 默认 30 秒；今日牌运卡海报按 300 秒（卡片是给人存图转发的）。"""
    if not isinstance(img, str) or not img:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # 无事件循环（测试环境）：不创建协程，避免悬空；启动清理兜底
    asyncio.create_task(_delayed_remove(img, delay))


def cleanup_stale_images() -> None:
    """启动时清空渲染临时目录：上次进程可能残留下没删的牌面图，
    只清 tarot_*.png / daily_*.png（不动目录里其他文件）。旧图留着也只是占地方。"""
    try:
        save_dir = os.path.join(tempfile.gettempdir(), "star_feather")
        if not os.path.isdir(save_dir):
            return
        for name in os.listdir(save_dir):
            if (name.startswith("tarot_") or name.startswith("daily_")) and name.endswith(".png"):
                try:
                    os.remove(os.path.join(save_dir, name))
                except OSError:
                    continue
    except Exception:
        pass

# ---------- 配色（官方牌背调亮 + 深色文字，白卡层次） ----------
CAPSULE_TEXT = (248, 249, 252)     # 胶囊内文字 白（标题与位置标签共用）
LINE = (130, 140, 170)           # 分隔线 灰蓝
CAPSULE_BG = (42, 54, 84)        # 胶囊底色 深藏青
RED = (190, 90, 90)              # 逆位标记
BAR_TITLE = (170, 128, 42)       # 正位 古铜金
BAR_TEXT = (70, 84, 100)         # 牌义 深蓝灰
CARD_BORDER = (200, 176, 120)    # 外卡描边 淡金

CARD_OUT_W = 340                 # 外层卡牌宽（含白边）
PAD_X = 18                       # 白边宽
INNER_W = CARD_OUT_W - PAD_X * 2 # 内部牌面图宽
INNER_H = 608                    # 内部牌面图区域统一高（素材 2:1；统一后各卡高度严格一致，
                                 # 布局不再依赖「第一张牌比例」——素材里 1.998 vs 2.0 的
                                 # 细微差异也会造成单元高度差 1px 的累计错位）
INFO_H = 104                     # 卡片底部信息区高
CARD_H = PAD_X * 2 + INNER_H + INFO_H  # 单张卡牌总高（白边上下 + 内图 + 信息区）

_ASSET_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_DIRS = {"wands": "Wands", "cups": "Cups", "swords": "Swords", "pentacles": "Pentacles"}
_ACE_SEP = {"wands": "_", "cups": "-", "swords": "_", "pentacles": "-"}
_ACE_EN = {"wands": "WANDS", "cups": "CUPS", "swords": "SWORDS", "pentacles": "COINS"}


# ---------- 文字绘制（字体加载与覆盖校验在 fonts.py） ----------
def _text_center(draw, cx, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _wrap_text(draw, text, font, max_w):
    """按像素宽度逐字换行（不是按字数——中英文混排宽度不同，按字数会溢出画布）。

    逐字累加直到超过 max_w 就断一行；文字本身不含换行符，
    长牌义词超过两行时由 _draw_card 截断，不会撑破卡片。
    """
    lines = []
    cur = ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


# ---------- 素材映射 ----------
def _resolve_asset(path: str) -> str:
    """兼容 .png / .webp 素材：优先已存在的文件，否则尝试另一后缀。"""
    if os.path.exists(path):
        return path
    if path.lower().endswith(".png"):
        alt = path[:-4] + ".webp"
    elif path.lower().endswith(".webp"):
        alt = path[:-5] + ".png"
    else:
        return path
    return alt if os.path.exists(alt) else path


def _asset_path(card) -> str:
    """tarot_data 牌元组 -> 素材文件绝对路径。"""
    suit, num, cn, en, up, down = card
    n = int(num)
    if suit == "major":
        # 素材文件里 3 号叫「女皇」，tarot_data 叫「皇后」——命名不同但都是她，认出来就行
        cn_fix = "女皇" if cn == "皇后" else cn
        fname = f"0-{cn_fix}.png" if n == 0 else f"{n:02d}-{cn_fix}.png"
        return _resolve_asset(os.path.join(_ASSET_ROOT, "MajorArcana", fname))
    if n == 1:
        sep = _ACE_SEP[suit]
        return _resolve_asset(os.path.join(_ASSET_ROOT, "Extra",
                            f"ACE OF {_ACE_EN[suit]}{sep}{SUIT_CN[suit]}王牌.png"))
    if n <= 10:
        return _resolve_asset(os.path.join(_ASSET_ROOT, _DIRS[suit],
                            f"{SUIT_CN[suit]}-{n:02d}.png"))
    court = {11: "侍从", 12: "骑士", 13: "王后", 14: "国王"}[n]
    return _resolve_asset(os.path.join(_ASSET_ROOT, _DIRS[suit], f"{SUIT_CN[suit]}{court}.png"))


def _draw_capsule(d, cx, cy, text, font, pad_x, pad_y, bg, fg):
    """深色胶囊底 + 白字，第一眼醒目。"""
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x0 = cx - tw / 2 - pad_x
    y0 = cy - th / 2 - pad_y
    d.rounded_rectangle([x0, y0, x0 + tw + 2 * pad_x, y0 + th + 2 * pad_y],
                        radius=int((th + 2 * pad_y) / 2), fill=bg)
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=fg)


# ---------- 画布背景 ----------

def _build_card_background(card, w, h) -> Image.Image:
    """牌面 cover 做底 + 深藏青遮罩——背景跟牌走（今日牌运卡与普通拼图共用）。

    cover 放大前先裁中心带：素材上下缘自带牌名/装饰文字，整图放大会把它们
    放大成底部大字；旧海报背景是纯净花纹+人物剪影，取中心 72% 避开。
    素材缺失抛 FileNotFoundError：渲染层 fail loudly，回退由编排层兜底。
    """
    path = _asset_path(card)
    if not os.path.exists(path):
        raise FileNotFoundError(f"塔罗素材缺失: {path}")
    src = Image.open(path).convert("RGBA")
    band = max(1, int(src.height * 0.72))
    band_top = (src.height - band) // 2
    src = src.crop((0, band_top, src.width, band_top + band))
    scale = max(w / src.width, h / src.height)
    bw, bh = int(src.width * scale) + 1, int(src.height * scale) + 1
    bg = src.resize((bw, bh), Image.LANCZOS)
    left, top = (bw - w) // 2, (bh - h) // 2
    canvas = bg.crop((left, top, left + w, top + h)).convert("RGBA")
    overlay = Image.new("RGBA", (w, h), (28, 34, 56, 178))  # 深藏青遮罩：花纹隐约、文字可读
    return Image.alpha_composite(canvas, overlay)


def _load_card_image(path: str, upright: bool):
    """加载并处理单张牌面：contain 缩放到 INNER_W×INNER_H 内（保持比例，不拉伸）、
    转 RGBA、逆位旋转 180°。返回 (img, w, h)（缩放后实际尺寸，居中用）。"""
    img = Image.open(path)
    scale = min(INNER_W / img.width, INNER_H / img.height)
    tw = max(1, int(img.width * scale))
    th = max(1, int(img.height * scale))
    img = img.resize((tw, th), Image.LANCZOS)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if not upright:
        img = img.rotate(180)
    return img, tw, th


def _draw_card(canvas, x, y, card, upright) -> int:
    """在 (x, y) 绘制一张白边卡牌（卡底 + 内图 + 信息区），返回单元总高度。

    内图在 INNER_W×INNER_H 区域内保持比例居中（素材 2:1 时恰好满幅无留白）；
    所有卡片单元高度严格一致，与 render_cards 的 unit_h 一致。
    """
    path = _asset_path(card)
    if not os.path.exists(path):
        raise FileNotFoundError(f"塔罗素材缺失: {path}")
    img, tw, th = _load_card_image(path, upright)

    card_h = CARD_H
    d = ImageDraw.Draw(canvas)

    # 外层白卡
    d.rounded_rectangle([x, y, x + CARD_OUT_W - 1, y + card_h - 1],
                        radius=14, fill=(252, 253, 255))
    d.rounded_rectangle([x, y, x + CARD_OUT_W - 1, y + card_h - 1],
                        radius=14, outline=CARD_BORDER, width=2)

    # 内图：统一区域垂直/水平居中放置
    off_x = (INNER_W - tw) // 2
    off_y = (INNER_H - th) // 2
    canvas.paste(img, (x + PAD_X + off_x, y + PAD_X + off_y), img)

    # 信息区：正逆位标记 + 牌名 + 牌义关键词
    suit, num, cn, en, up, down = card
    info_y = y + PAD_X + INNER_H
    d.line([(x + 20, info_y + 10), (x + CARD_OUT_W - 20, info_y + 10)],
           fill=LINE, width=2)

    tag = "正位" if upright else "逆位"
    tag_color = BAR_TITLE if upright else RED
    _text_center(d, x + CARD_OUT_W / 2, info_y + 20, f"{tag} · {cn}",
                 _load_font(25, bold=True, text=f"{tag} · {cn}"), tag_color)

    meaning = up if upright else down
    # 信息区高 104 只放得下两行（起始 y+54、行距 25），
    # 第三行起截去——牌义关键词均已控制在两行内，截断只为兜底生僻超长文案
    lines = _wrap_text(d, meaning, _load_font(22, text=meaning), CARD_OUT_W - 44)
    for i, line in enumerate(lines[:2]):
        _text_center(d, x + CARD_OUT_W / 2, info_y + 54 + i * 25, line,
                     _load_font(22, text=line), BAR_TEXT)

    return card_h


# ---------- 拼图主函数 ----------
def render_cards(positions, picks, formation, save_dir=None) -> str:
    """把抽牌结果渲染成一张图片，返回图片保存路径。

    背景跟牌走：从本签抽出的 picks 里随机挑一张的牌面做底（3 张阵 3 选 1、
    十字阵 4 选 1），与今日牌运卡同一套风味；素材缺失抛异常，由 tarot_core
    回退纯文本。清理与渲染解耦：调用方在拿到路径后自行
    _schedule_image_cleanup（tarot_core._maybe_render_image / daily 编排）。
    """
    n = len(picks)
    cols = 1 if n <= 1 else (2 if n == 4 else n)
    rows = (n + cols - 1) // cols

    label_h = 70
    pad = 36
    gap = 28
    title_h = 96

    # 单元高度：统一常量（不再以第一张牌素材比例为准）
    unit_h = CARD_H

    W = cols * CARD_OUT_W + (cols - 1) * gap + pad * 2
    H = title_h + rows * (label_h + unit_h) + (rows - 1) * gap + pad * 2

    # 背景从本签抽出的牌里随机挑一张做底（3 张阵 3 选 1、十字阵 4 选 1）——
    # 每签背景都可能是本签任意一张的牌面，跟今日牌运卡同为「本签的牌」定基调
    canvas = _build_card_background(random.choice(picks)["card"], W, H)
    d = ImageDraw.Draw(canvas)

    _draw_capsule(d, W / 2, 52, f"牌阵 · {formation}", _load_font(38, bold=True, text=f"牌阵 · {formation}"),
                  36, 14, CAPSULE_BG, CAPSULE_TEXT)

    for i, (pos, pick) in enumerate(zip(positions, picks)):
        col = i % cols
        row = i // cols
        x = pad + col * (CARD_OUT_W + gap)
        y = title_h + row * (label_h + unit_h + gap)
        _draw_capsule(d, x + CARD_OUT_W / 2, y + 40, f"【{pos}】",
                      _load_font(28, bold=True, text=f"【{pos}】"), 20, 10, CAPSULE_BG, CAPSULE_TEXT)
        _draw_card(canvas, x, y + label_h, pick["card"], pick["upright"])

    if save_dir is None:
        save_dir = os.path.join(tempfile.gettempdir(), "star_feather")
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, f"tarot_{int(time.time())}_{secrets.token_hex(4)}.png")
    canvas.save(path, "PNG")
    return path


# ---------- 今日牌运卡海报 ----------
DAILY_CARD_W = 680                       # 海报宽（竖版）
DAILY_CARD_H = 1200                      # 海报高
_DAILY_SIG_TEXT = (236, 227, 196)        # 签文引用 米金
_DAILY_DATE_TEXT = (208, 214, 226)       # 日期 灰白
_DAILY_SIGN_TEXT = (186, 194, 208)       # 署名 灰蓝
_DAILY_FOOT_TEXT = (236, 188, 204)       # 水印 樱粉（本羽喜欢的颜色）


def _split_signature_lines(d, text, font, max_w) -> list[str]:
    """签文分行：优先在标点（，。！？；、）后断、标点收尾，单句仍超宽再逐字回落。

    断行不丢字不截断（调用方渲染时最多取两行，池内上限 32 字必放下）；
    短签文（约 18 字）一行居中——旧版牌运卡的秀气观感，别逐字硬切成半句。
    """
    segs, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "，。！？；、":
            segs.append(cur)
            cur = ""
    if cur:
        segs.append(cur)
    lines, line = [], ""
    for seg in segs:
        if d.textlength(line + seg, font=font) <= max_w:
            line += seg
            continue
        if line:
            lines.append(line)
            line = ""
        while d.textlength(seg, font=font) > max_w:  # 单段超宽：逐字切回落到两行以上
            cut = 0
            while cut < len(seg) and d.textlength(seg[:cut + 1], font=font) <= max_w:
                cut += 1
            lines.append(seg[:cut])
            seg = seg[cut:]
        line = seg
    if line:
        lines.append(line)
    return lines


def _render_daily_card_img(card, upright, signature, date_text, save_dir=None) -> str:
    """今日牌运卡海报：同款牌面素材放大做底 + 深色遮罩，竖版 680×1200。

    自上而下：标题胶囊「星羽塔罗·今日牌运」/ 日期（如「8月27日·周四」）/
    白边卡牌（_draw_card 同款，逆位卡内旋转而背景不转）/ 签文引用「『…』」/
    署名「—— 牌灵·星羽塔罗」/ 底部水印。同步函数：编排在 daily.render_daily_card
    经 to_thread 调用；素材缺失抛异常，由编排层兜底回退普通牌面图。
    """
    W, H = DAILY_CARD_W, DAILY_CARD_H
    canvas = _build_card_background(card, W, H)
    d = ImageDraw.Draw(canvas)

    _draw_capsule(d, W / 2, 72, "星羽塔罗·今日牌运",
                  _load_font(34, bold=True, text="星羽塔罗·今日牌运"),
                  36, 14, CAPSULE_BG, CAPSULE_TEXT)
    _text_center(d, W / 2, 124, date_text,
                 _load_font(26, text=date_text), _DAILY_DATE_TEXT)

    card_x = (W - CARD_OUT_W) // 2
    card_y = 176
    _draw_card(canvas, card_x, card_y, card, upright)

    # 签文引用：24px 常规体（秀气，旧卡同款观感），按标点分行最多两行；
    # 署名与底部水印固定留白
    quote = f"『{signature}』"
    q_font = _load_font(24, text=quote)
    qy = 992
    for line in _split_signature_lines(d, quote, q_font, W - 200)[:2]:
        _text_center(d, W / 2, qy, line, q_font, _DAILY_SIG_TEXT)
        qy += 38
    _text_center(d, W / 2, qy + 12, "—— 牌灵·星羽塔罗",
                 _load_font(23, text="—— 牌灵·星羽塔罗"), _DAILY_SIGN_TEXT)
    _text_center(d, W / 2, H - 58, "星羽塔罗 Star Feather",
                 _load_font(21, text="星羽塔罗 Star Feather"), _DAILY_FOOT_TEXT)

    if save_dir is None:
        save_dir = os.path.join(tempfile.gettempdir(), "star_feather")
    os.makedirs(save_dir, exist_ok=True)
    out_path = os.path.join(save_dir, f"daily_{int(time.time())}_{secrets.token_hex(4)}.png")
    canvas.convert("RGB").save(out_path, "PNG")
    return out_path
