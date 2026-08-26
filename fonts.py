"""字体子系统：候选链、字形覆盖校验与加载缓存。

独立于渲染布局（card_render）：字体怎么找、缺字怎么回退、缓存什么规则，
都是「字体」这一个关注点；card_render 只消费 _load_font 的结果。

优先级：内置主字体（Noto Sans SC 子集，品牌化命名 StarFeather-*）>
系统字体 > 默认（可能缺中文字形）。粗体使用独立的 StarFeather-Bold 子集。
（想写得好看，先过字体这一关——本羽的排面不能是豆腐块。）
"""
import functools
import os

from PIL import ImageFont

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

_FONT_CACHE = {}   # (size, bold) -> ImageFont（仅不带 text 的选择可入缓存）
_FONT_CMAP = {}    # path -> set(ord) | None（无法解析时放行）


@functools.cache
def _font_candidates(bold: bool) -> tuple[str, ...]:
    # 内置子集缺字（GB2312 之外的生僻字）时回退系统字体。
    # cache：候选列表是常量，每次调用重建纯属浪费；只读遍历，不会污染缓存对象。
    return (
        os.path.join(_FONT_DIR, "StarFeather-Bold.otf" if bold else "StarFeather-Regular.otf"),
        # Windows
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        # Linux (常见发行版)
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    )


def _font_covers(font, text: str) -> bool:
    """校验字形覆盖：内置子集命中后仍检查文本中每个字符是否在 cmap 内，
    缺字时回退链继续找下一个候选（避免未来新增生僻字牌义变豆腐块）。
    无 fontTools / 无法解析时保守放行。"""
    path = getattr(font, "path", None)
    if not path or not text:
        return True
    cmap = _FONT_CMAP.get(path)
    if cmap is None:
        try:
            from fontTools.ttLib import TTFont
            tf = TTFont(path, lazy=True)
            cmap = set()
            for table in tf["cmap"].tables:
                if table.isUnicode():
                    cmap.update(table.cmap.keys())
            tf.close()
        except Exception:
            cmap = None  # 解析失败/无 fontTools：视为全覆盖，不拦截
        _FONT_CMAP[path] = cmap
    if cmap is None:
        return True
    return all(ord(c) in cmap for c in text if not c.isspace())


def _load_font(size: int, bold: bool = False, text: str = None):
    """加载中文字体，优先级：内置字体(fonts/) > 系统字体 > 默认(可能缺中文字形)。

    内置字体为 Noto Sans SC 子集（品牌化命名 StarFeather-*.otf，覆盖牌面文案），
    使渲染跨平台一致（Windows / Linux / macOS 均不会因系统缺中文字体而显示方块）。
    传入 text 时做字形覆盖校验，内置子集缺字（GB2312 之外的生僻字）自动回退到
    系统字体。粗体使用独立的 StarFeather-Bold 子集。

    缓存规则：只有不带 text 的调用才写 _FONT_CACHE（此时选中的必是候选链
    第一个可加载字体=内置）。带 text 的调用结果依赖文本内容（生僻字可能
    回退到系统字体），一旦缓存会污染全局后续渲染——例如先渲染含生僻字的
    卡片会把 msyh 永久缓存，之后所有同 size 渲染都错失内置字体。
    （说白了：带字的按次算账，不带字的才许进缓存——别嫌本羽抠门。）
    """
    key = (size, bold)
    font = _FONT_CACHE.get(key)
    if font is not None and (text is None or _font_covers(font, text)):
        return font
    for path in _font_candidates(bold):
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            continue
        if text is None or _font_covers(font, text):
            if text is None:  # 无 text 的选择可全局复用；带 text 的选择依赖文本，不写缓存
                _FONT_CACHE[key] = font
            return font
    font = ImageFont.load_default()
    if text is None:
        _FONT_CACHE[key] = font
    return font
