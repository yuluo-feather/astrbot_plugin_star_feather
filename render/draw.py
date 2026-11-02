"""Pillow 绘制：牌面图与今日牌运海报。

画布尺寸与坐标在 render/layout.py，素材清单在 render/assets.py，
临时文件生命周期在 render/store.py。
"""
import functools
import os
import random
import secrets
import time

from PIL import Image, ImageDraw

from ..core.tarot_data import meaning_text
from ..core.texts import (
    CARD_TAG_REVERSED,
    CARD_TAG_UPRIGHT,
    DAILY_SIGNATURE,
    DAILY_TITLE,
    DAILY_WATERMARK,
    FORMATION_TITLE,
)
from .assets import _asset_path, _asset_path_by_id
from .fonts import _load_font
from .layout import (
    BAR_TEXT,
    BAR_TITLE,
    CAPSULE_BG,
    CAPSULE_TEXT,
    CARD_BORDER,
    CARD_H,
    CARD_OUT_W,
    DAILY_CARD_H,
    DAILY_CARD_QUOTE_Y,
    DAILY_CARD_W,
    DAILY_CARD_Y,
    DAILY_DATE_TEXT,
    DAILY_FOOT_TEXT,
    DAILY_SIG_TEXT,
    DAILY_SIGN_TEXT,
    INNER_H,
    INNER_W,
    LABEL_H,
    LINE,
    PAD_X,
    RED,
    card_grid,
)
from .store import IMAGE_DIR_ROOT

_RNG = random.Random()
_RESAMPLE = Image.Resampling.LANCZOS

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
    # 先量整段：放得下就是单行（逐字累加的最后一测量正是整段宽度，放得下即从不越界）
    if draw.textlength(text, font=font) <= max_w:
        return [text] if text else []
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
    # 均衡断行：最后一行只剩一个字（「延迟的快 / 乐」）时从上一行借一个字下来，
    # 两行都不至于孤零零。只在两行时做——三行起后两行会被 _draw_card 的 [:2] 截掉。
    if (len(lines) == 2 and len(lines[-1]) == 1 and len(lines[0]) > 1
            and draw.textlength(lines[0][-1] + lines[-1], font=font) <= max_w):
        lines[-1] = lines[0][-1] + lines[-1]
        lines[0] = lines[0][:-1]
    return lines


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

@functools.lru_cache(maxsize=4)
def _background_cached(card_id: str, w: int, h: int) -> Image.Image:
    """牌面 cover 做底 + 深藏青遮罩——背景跟牌走（今日牌运卡与普通拼图共用）。

    cover 放大前先裁中心带：素材上下缘自带牌名/装饰文字，整图放大会把它们
    放大成底部大字；旧海报背景是纯净花纹+人物剪影，取中心 72% 避开。
    素材缺失抛 FileNotFoundError：渲染层 fail loudly，回退由编排层兜底。
    """
    path = _asset_path_by_id(card_id)
    if not os.path.exists(path):
        raise FileNotFoundError(f"塔罗素材缺失: {path}")
    # with 收口：convert() 已经把像素读进来，文件句柄不必拖到 GC（WebP 句柄尤其要收）
    with Image.open(path) as raw:
        src = raw.convert("RGBA")
    band = max(1, int(src.height * 0.72))
    band_top = (src.height - band) // 2
    src = src.crop((0, band_top, src.width, band_top + band))
    scale = max(w / src.width, h / src.height)
    bw, bh = int(src.width * scale) + 1, int(src.height * scale) + 1
    bg = src.resize((bw, bh), _RESAMPLE)
    left, top = (bw - w) // 2, (bh - h) // 2
    canvas = bg.crop((left, top, left + w, top + h)).convert("RGBA")
    overlay = Image.new("RGBA", (w, h), (28, 34, 56, 178))  # 深藏青遮罩：花纹隐约、文字可读
    return Image.alpha_composite(canvas, overlay)


def _build_card_background(card, w, h) -> Image.Image:
    """背景成品缓存入口：按「牌 + 尺寸」缓存，返回可画的副本。

    key 含牌面信息（底牌跟本签的牌走，不能改成只按尺寸缓存），所以 maxsize 的含义是
    「最近 N 个 (牌, 尺寸) 组合」而不是「N 种尺寸」，并且**跨签基本不命中**：每次渲染只挑
    一张底牌进缓存，要遇到 4 张不同的牌（同尺寸）才填满。真实收益在「同一签反复渲染」
    （用户连着问几次）与「当天牌固定的海报」上——这是「背景跟牌走」的必然代价。
    单档实测（本机 RGBA 实算，非估算）：1 张 412×986 = 1.55 / 3 张 1148×986 = 4.32 /
    4 张 780×1832 = 5.45 / 海报 680×1200 = 3.11 MiB；上界 = 4 × 最大单档 ≈ 21.8 MiB。
    红线：tests/test_card_render.py::TestBackgroundCacheSizing
    """
    return _background_cached(card["id"], w, h).copy()


@functools.lru_cache(maxsize=8)
def _load_card_image(path: str, upright: bool):
    """加载并处理单张牌面：contain 缩放到 INNER_W×INNER_H 内（保持比例，不拉伸）、
    转 RGBA、逆位旋转 180°。返回 (img, w, h)（缩放后实际尺寸，居中用）。

    结果按 (路径, 正逆) 缓存（LRU 8，约 6MB）：这一步是高清素材解码 + LANCZOS 缩放，
    实测本机 50~80ms/张（随负载浮动）——同一张牌再次出现（连续占卜、同群多签）时直接命中。
    返回的图是缓存本体，调用方只许 paste（不得就地改画）。"""
    # 单张解码在 with 里完成：resize/convert 产出的都是新图，句柄可以当场关掉
    with Image.open(path) as raw:
        scale = min(INNER_W / raw.width, INNER_H / raw.height)
        tw = max(1, int(raw.width * scale))
        th = max(1, int(raw.height * scale))
        img: Image.Image = raw.resize((tw, th), _RESAMPLE)
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

    # 信息区：正逆位标记 + 牌名 + 牌义关键词（brief：只关键词，两行内）
    cn = card["cn"]
    info_y = y + PAD_X + INNER_H
    d.line([(x + 20, info_y + 10), (x + CARD_OUT_W - 20, info_y + 10)],
           fill=LINE, width=2)

    tag = CARD_TAG_UPRIGHT if upright else CARD_TAG_REVERSED
    tag_color = BAR_TITLE if upright else RED
    _text_center(d, x + CARD_OUT_W / 2, info_y + 20, f"{tag} · {cn}",
                 _load_font(25, bold=True, text=f"{tag} · {cn}"), tag_color)

    meaning = meaning_text(card, upright, brief=True)
    # 信息区高 104 只放得下两行（起始 y+54、行距 25），
    # 第三行起截去——牌义关键词均已控制在两行内，截断只为兜底生僻超长文案
    # 量宽与逐行绘制共用同一字体对象：分行是 meaning 的子串，字形覆盖必然一致；
    # 取同一个实例既免去重复加载，也让「量的时候放得下、画的时候溢出」在结构上
    # 不可能发生（此前两处各自 _load_font，能不能相等是隐式前提而非保证）。
    mean_font = _load_font(22, text=meaning)
    lines = _wrap_text(d, meaning, mean_font, CARD_OUT_W - 44)
    for i, line in enumerate(lines[:2]):
        _text_center(d, x + CARD_OUT_W / 2, info_y + 54 + i * 25, line, mean_font, BAR_TEXT)

    return card_h


# ---------- 拼图主函数 ----------
# 输出编码：JPEG q88。同一张牌阵 PNG 是 0.6~2.0 MB、JPEG 是 0.13~0.47 MB，
# 合并转发要在协议端上传图片，体积直接换上传时间（2026-11-01 实测）。
OUTPUT_QUALITY = 88
# 铺底色：素材边缘带半透明（实测画布 alpha 最低 191），直接 convert("RGB") 会把
# 半透明像素按黑底算，边缘出一圈脏黑。先用同款深藏青铺实，再合成。
_OPAQUE_BACKDROP = (28, 34, 56)


def _save_image(image, path: str) -> None:
    """保存为 JPEG（先铺实色底再转 RGB）；目标目录若恰在保存前被启动清理回收，就重建再存。

    目录重建只兜「热重载时旧实例正在渲染」这个极窄窗口；第二次仍失败就让异常上抛，
    由渲染兜底转成无图。
    """
    if image.mode != "RGB":
        base = Image.new("RGBA", image.size, (*_OPAQUE_BACKDROP, 255))
        image = Image.alpha_composite(base, image.convert("RGBA")).convert("RGB")
    try:
        image.save(path, "JPEG", quality=OUTPUT_QUALITY)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        image.save(path, "JPEG", quality=OUTPUT_QUALITY)


def render_cards(positions, picks, formation, save_dir=None) -> str:
    """把抽牌结果渲染成一张图片，返回图片保存路径。

    背景跟牌走：从本签抽出的 picks 里随机挑一张的牌面做底（3 张阵 3 选 1、
    十字阵 4 选 1），与今日牌运卡同一套风味；素材缺失抛异常，由 services.render
    回退纯文本。清理与渲染解耦：调用方在拿到路径后自行
    _schedule_image_cleanup（services.render 编排）。
    """
    grid = card_grid(len(picks))
    W, H = grid.width, grid.height

    # 背景从本签抽出的牌里随机挑一张做底（3 张阵 3 选 1、十字阵 4 选 1）——
    # 每签背景都可能是本签任意一张的牌面，跟今日牌运卡同为「本签的牌」定基调
    canvas = _build_card_background(_RNG.choice(picks)["card"], W, H)
    d = ImageDraw.Draw(canvas)

    _draw_capsule(d, W / 2, 52, FORMATION_TITLE.format(formation=formation), _load_font(38, bold=True, text=FORMATION_TITLE.format(formation=formation)),
                  36, 14, CAPSULE_BG, CAPSULE_TEXT)

    for i, (pos, pick) in enumerate(zip(positions, picks)):
        x, y = grid.origin(i)
        _draw_capsule(d, x + CARD_OUT_W / 2, y + 40, f"【{pos}】",
                      _load_font(28, bold=True, text=f"【{pos}】"), 20, 10, CAPSULE_BG, CAPSULE_TEXT)
        _draw_card(canvas, x, y + LABEL_H, pick["card"], pick["upright"])

    if save_dir is None:
        # 生产路径一律显式传实例目录（Renderer.image_dir，见 new_instance_image_dir）；
        # 这里只是「没有实例上下文」的兜底（离线脚本、单函数测试），
        # 落回根目录的文件由启动清理按过期判据回收
        save_dir = IMAGE_DIR_ROOT
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, f"tarot_{int(time.time())}_{secrets.token_hex(4)}.jpg")
    _save_image(canvas, path)
    return path


# ---------- 今日牌运卡海报 ----------
# 尺寸、坐标与颜色在 render/layout.py；签文起点由卡片高度推导，勿写死数值。


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
            if cut == 0:
                # 单个字形就宽于画布：不兜这一下 cut 会恒为 0、seg 长度不减 → 死循环。
                # 现网尺寸（max_w=480、字号 24）不可达，但本函数跑在 to_thread 里，
                # 一旦触发是静默挂死一个线程池槽位，硬切一字保底。
                cut = 1
            lines.append(seg[:cut])
            seg = seg[cut:]
        line = seg
    if line:
        lines.append(line)
    return lines


def render_daily_card(card, upright, signature, date_text, save_dir=None) -> str:
    """今日牌运卡海报：同款牌面素材放大做底 + 深色遮罩，竖版 680×1200。

    自上而下：标题胶囊「星羽塔罗·今日牌运」/ 日期（如「8月27日·周四」）/
    白边卡牌（_draw_card 同款，逆位卡内旋转而背景不转）/ 签文引用「『…』」/
    署名「—— 牌灵·星羽塔罗」/ 底部水印。同步函数：编排在 daily.render_daily_card
    经 to_thread 调用；素材缺失抛异常，由编排层兜底回退普通牌面图。
    """
    W, H = DAILY_CARD_W, DAILY_CARD_H
    canvas = _build_card_background(card, W, H)
    d = ImageDraw.Draw(canvas)

    _draw_capsule(d, W / 2, 72, DAILY_TITLE,
                  _load_font(34, bold=True, text=DAILY_TITLE),
                  36, 14, CAPSULE_BG, CAPSULE_TEXT)
    _text_center(d, W / 2, 124, date_text,
                 _load_font(26, text=date_text), DAILY_DATE_TEXT)

    card_x = (W - CARD_OUT_W) // 2
    card_y = DAILY_CARD_Y
    _draw_card(canvas, card_x, card_y, card, upright)

    # 签文引用：24px 常规体（秀气，旧卡同款观感），按标点分行最多两行；
    # 署名与底部水印固定留白
    quote = f"『{signature}』"
    q_font = _load_font(24, text=quote)
    qy = DAILY_CARD_QUOTE_Y
    for line in _split_signature_lines(d, quote, q_font, W - 200)[:2]:
        _text_center(d, W / 2, qy, line, q_font, DAILY_SIG_TEXT)
        qy += 38
    _text_center(d, W / 2, qy + 12, DAILY_SIGNATURE,
                 _load_font(23, text=DAILY_SIGNATURE), DAILY_SIGN_TEXT)
    _text_center(d, W / 2, H - 58, DAILY_WATERMARK,
                 _load_font(21, text=DAILY_WATERMARK), DAILY_FOOT_TEXT)

    if save_dir is None:
        # 生产路径一律显式传实例目录（Renderer.image_dir，见 new_instance_image_dir）；
        # 这里只是「没有实例上下文」的兜底（离线脚本、单函数测试），
        # 落回根目录的文件由启动清理按过期判据回收
        save_dir = IMAGE_DIR_ROOT
    os.makedirs(save_dir, exist_ok=True)
    out_path = os.path.join(save_dir, f"daily_{int(time.time())}_{secrets.token_hex(4)}.jpg")
    _save_image(canvas, out_path)
    return out_path
