"""星羽塔罗 - 牌面图片渲染模块

使用 B 站幻星集主题塔罗牌素材（assets/）渲染牌面：
- 所有牌统一「白边卡牌」样式：外层白色圆角卡底 + 内部牌面图，视觉整齐
- 画布背景为本签随机一张牌面放大做底 + 深藏青遮罩（与今日牌运卡同款风味）
- 逆位时内部牌面图旋转 180°，信息区标注正逆位 + 牌名 + 牌义关键词
素材缺失时抛出异常，由上层（tarot_core）回退纯文本牌面。

——本羽最得意的部分：牌要出得好看，才配得上占卜。
"""
import asyncio
import functools
import itertools
import os
import random
import re
import secrets
import shutil
import tempfile
import time

from astrbot.api import logger
from PIL import Image, ImageDraw

from .fonts import _load_font
from .tarot_data import SUIT_CN, meaning_text

# 背景选牌用模块私有 RNG：不消耗全局随机状态（全局那位留给「谁先谁后无所谓」的调用方，
# 与 daily._daily_pick 的独立 RNG 同一口径），渲染也因此不扰动全局随机序——
# 「同输入同 md5」的确定性渲染对比不会被它搅乱。
# 不用 secrets：这里要的是「每次不同」而非「不可预测」，私有 Random 语义更贴。
_RNG = random.Random()

# 缩放重采样口径：用 Resampling 枚举（9.1 起），不用模块级别名 Image.LANCZOS——
# 别名在 Pillow 12 的类型标注里已经不在（运行期还在，纯静态报错），枚举写法在
# 本仓库声明的 Pillow 底线 10.0.0 上就有，走它省得每次升级被静态检查咬一口。
_RESAMPLE = Image.Resampling.LANCZOS


# ---------- 产物生命周期：渲染出的临时图片由本模块统一管理 ----------
IMAGE_TTL_SECONDS = 600
"""临时图存活上限（秒）。定位是**防堆积的保险丝**，不是正确性机制。

判据从来不是「渲染后过了几秒」，而是**发送那一刻文件还在不在**——平台是在发送
那一刻才读文件的（2026-09-29 实机日志里 WebChat 就在那一步 convert_to_base64），
而渲染曾经发生在两次 AI 调用之前（牌灵的话 + 解读），那时的窗口由 ai_timeout ×
候选数 × 2 段决定，没有上界：那次一句「牌灵的话」的 AI 请求实测跑了 63 秒，图早就
被删掉了，平台读到空，整条消息链（图 + 解读 + 牌灵的话）一起被打回（Issue #2）。
那之后窗口收窄了两轮：AI 阶段有了总预算（ai.ai_budget_seconds）、② 图片后置渲染把
渲染点搬到两段 AI 之后（2026-10-12），距离平台读文件只剩毫秒级。

所以本常量只管「最坏情况下临时目录里堆多少」——一次占卜一张 2~3MB、600 秒的量级
在容器里无压力，插件启动时还会回收过期实例目录；真正的兜底在发送前：main._ensure_image。
别把它调小去省磁盘，省下的那点空间换不来一次丢消息。
"""


async def _delayed_remove(path: str, delay: int = IMAGE_TTL_SECONDS) -> None:
    """到点删图（保险丝，见 IMAGE_TTL_SECONDS）：既不耽误发送，也不让临时目录无限堆积。"""
    await asyncio.sleep(delay)
    try:
        os.remove(path)
    except OSError:
        pass  # 已被启动清理/其他路径处理，无需管


def _schedule_image_cleanup(img: str | None, delay: int = IMAGE_TTL_SECONDS) -> None:
    """图片产生点即绑定清理（而不是绑定发送出口）：无论后续走 AI 成功、
    文字兜底还是异常中断，任务都已在渲染成功后注册完毕；
    不在事件循环（如测试）或进程被杀时，由启动清理兜底。

    delay 统一用 IMAGE_TTL_SECONDS：普通牌面图与今日牌运海报卡曾各写一个
    （30 / 300 秒），其实是同一件事——内容在发送那一刻就被平台取走了，本地
    文件之后活多久与用户无关，两个数字只会多一个会过时的点。

    契约：本函数永不抛。它只是保险丝，代价必须比它保的东西便宜——登记失败最多
    留一张临时图（启动清理兜底，见 cleanup_stale_images），而抛出去会顺着调用点
    一路顶到入口，把整条卦带走（tarot_core._maybe_render_image 的调用点就裸在
    渲染兜底之外）。

    入参允许 None / 空串并原地返回：渲染失败的返回值就是 None（tarot_core._render_image），
    调用点不必自己先判一遍——判在这儿，一个判据、一个地方。签名不写窄，才敢让
    None 一路传进来。
    """
    if not isinstance(img, str) or not img:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # 无事件循环（测试环境）：不创建协程，避免悬空；启动清理兜底
    task = _delayed_remove(img, delay)
    try:
        asyncio.create_task(task)
    except Exception as e:
        # 循环已在收尾（AstrBot 热重载 / 进程退出）：get_running_loop 还答得出话，
        # create_task 却抛 RuntimeError('cannot schedule new futures after shutdown')。
        # 先 close 掉刚建出来的协程——不关就是一条「never awaited」告警进日志，
        # 明明只是少个保险丝，别顺手往日志里丢噪声。
        task.close()
        logger.warning(f"牌面图清理登记失败（保险丝失效，图留待启动清理）: {e}")


# ---------- 实例图片目录：每个插件实例一份，互不打扫对方 ----------
IMAGE_DIR_ROOT = os.path.join(tempfile.gettempdir(), "star_feather")
"""图片根目录。实例目录是它的子目录（见 new_instance_image_dir）。

【为什么要把「一个共享目录」拆成「每实例一份」，2026-10-12 ①】
旧实现每次构造插件实例都清空这个根目录。单体运行看不出问题，热重载就露馅：
新实例在 __init__ 里打扫，旧实例可能还活着——它刚渲染完、图还在等发送，
被新实例一删，平台读到空文件，整条消息链一起被打回（与 Issue #2 同一条死法，
只是这次的凶手不是保险丝超时，而是隔壁那个刚出生的自己）。
静态审查称它为「理论竞争窗口」，实际路径是确定性顺序：热重载 → 新实例清扫父目录
→ 删掉旧实例正在用的图。改成按实例分目录后，打扫者只看得见自己的目录。
"""

_INSTANCE_DIR_RE = re.compile(r"^inst_(\d+)_\d+_[0-9a-f]{8}$")
"""实例目录命名：inst_<pid>_<进程内序号>_<随机 8 位十六进制>（pid 分组捕获）。

三段各有职责：pid 区分进程（重启后的残骸一眼能认）；序号区分**同一进程内**
先后或并存的两代实例（热重载正是同 pid——只靠 pid 的名字区分不了它们，
而它们恰恰是本案的主角）；随机尾防撞名。pid 必须可解析：清理时按它跳过**本
进程的全部实例目录**，见 cleanup_stale_images。
"""

STALE_INSTANCE_DIR_AGE = 3 * 24 * 3600
"""目录「可以回收」的年龄判据（秒）：三天。只用来挑磁盘回收候选，不参与正确性。

判据取目录自身的 mtime，即「里面最后一次新增/删除文件距今多久」。它是个**启发式**，
不是「进程已退出」「目录已无人使用」的证明：mtime 只说明「最近三天没往这里写过东西」。
之所以敢据此回收，是因为图片是一次性产物、渲染到发送的间隔以分钟计——一个三天没写过
新东西的目录里，极少还躺着一条正在等发送的图。它撑不住的正确性由另外两条兜住：
同进程的其他实例目录一律免删（见 cleanup_stale_images 的 pid 判据），跨进程那份由
发送前的 main._ensure_image 补渲染兜底。
为什么不设更短：判错一次的代价是删掉别人可能正在用的图——正是本案要修的东西；
多留几天的代价只是几十 MB 磁盘。这个不对称决定宁可宽松。
"""

_INSTANCE_SEQ = itertools.count(1)
"""进程内实例序号：只增不减。热重载一次 +1，两代实例因此永远不同名。"""


def new_instance_image_dir() -> str:
    """为当前插件实例开一份专属图片目录，返回其路径。

    主路径：`IMAGE_DIR_ROOT/inst_<pid>_<序号>_<随机尾>`（撞名就换一个，绝不复用
    别人的目录）。根目录建不出来时**不退回共享目录**——那正是①要修掉的「共用一份」，
    新实例在那里读写会把别的实例的图搅在一起；改在系统临时目录下开一个唯一目录
    （见 `_private_fallback_dir`）。每一档都尽量保住「每实例一份」这条不变量。
    """
    try:
        os.makedirs(IMAGE_DIR_ROOT, exist_ok=True)
    except OSError as e:
        logger.warning(f"图片根目录不可用（{IMAGE_DIR_ROOT}），本次实例改用进程私有临时目录: {e}")
        return _private_fallback_dir()
    for _ in range(8):
        name = f"inst_{os.getpid()}_{next(_INSTANCE_SEQ)}_{secrets.token_hex(4)}"
        path = os.path.join(IMAGE_DIR_ROOT, name)
        try:
            os.makedirs(path, exist_ok=False)  # 撞名就换一个：绝不复用别人的目录
            return path
        except FileExistsError:
            continue
        except OSError as e:
            logger.warning(f"实例图片目录创建失败（{path}），本次实例改用进程私有临时目录: {e}")
            return _private_fallback_dir()
    logger.warning("实例图片目录连续撞名，本次实例改用进程私有临时目录")
    return _private_fallback_dir()


def _private_fallback_dir() -> str:
    """根目录不可用时的退路：在系统临时目录下开一个**唯一**目录（mkdtemp 保证不撞名）。

    为什么不用 `tempfile.gettempdir()` 或共享的 `IMAGE_DIR_ROOT`：那两个都是「共用
    一份」，新实例一写就把别的实例的图搅在一起（①的原始病灶）。退路也必须是每实例
    一份，哪怕它落在根目录之外。它不在 IMAGE_DIR_ROOT 之下，启动清理扫不到，清理
    靠 600 秒保险丝。

    连这个都建不出来＝磁盘/权限已经彻底不可用，返回空串让渲染安全降级为无图
    （调用方与渲染器都容得下空路径）：此时「隔离」让位于「别再把整条消息带走」。
    """
    try:
        return tempfile.mkdtemp(prefix=f"star_feather_inst_{os.getpid()}_")
    except OSError as e:
        logger.warning(f"实例图片目录的临时退路也不可用（{e}）：本实例图片输出降级为无图")
        return ""


def _is_stale(path: str, max_age: int) -> bool:
    """是否超过回收年龄：mtime 距今超过 max_age。stat 失败一律当不过期（保守放行）。"""
    try:
        return (time.time() - os.stat(path).st_mtime) > max_age
    except OSError:
        return False


def cleanup_stale_images(current_dir: str | None = None) -> None:
    """启动时回收**过期的残骸**（只当磁盘回收，不当作「已无人使用」的证明），别的什么都不碰。

    两条判据缺一不可：
    1. 命名合规（旧布局的 tarot_*.png / daily_*.png，或 _INSTANCE_DIR_RE 匹配的实例目录）；
    2. mtime 超过 STALE_INSTANCE_DIR_AGE（三天没写过新东西，只当磁盘回收候选）。

    豁免（按优先级）：
    - **本进程的全部实例目录一律跳过**——从名字里解析 pid，凡是 os.getpid() 的一律
      不碰。热重载正是「同进程、两代实例」：新实例启动清理时，旧实例可能还活着、
      图还在等发送，只跳过传入的 current_dir 是不够的（旧写法就是这么漏的，issue #21
      ① 复核已复现）。同进程的目录里可能还有图在等发送，宁可留着让保险丝去收。
    - current_dir 是本次传入的当前实例目录，同样永不删——即便系统时钟被往回调、
      让它的 mtime 看起来已经过期。
    跨进程的过期目录照旧回收（那些 pid 与本进程不同，是重启留下的残骸）。

    与旧实现的关键差别就一句：**旧版清空整个父目录，本版只删判据允许的部分**。
    父目录里不合规的文件（用户或别的程序放的）一律不动：这不是「顺手不删」，
    而是判据的一部分——一个插件没资格替宿主决定哪些文件算垃圾。

    契约：本函数永不抛（启动清理失败不该挡住插件加载），失败只留 warning。
    """
    if not os.path.isdir(IMAGE_DIR_ROOT):
        return
    try:
        entries = os.listdir(IMAGE_DIR_ROOT)
    except OSError as e:
        logger.warning(f"图片目录枚举失败，跳过启动清理: {e}")
        return

    own_pid = os.getpid()
    reaped = 0
    for name in entries:
        path = os.path.join(IMAGE_DIR_ROOT, name)
        try:
            if name.startswith("tarot_") or name.startswith("daily_"):
                # 旧布局散图（本版起生产路径不再往父目录写图）：同样「确认过期才删」
                if name.endswith(".png") and os.path.isfile(path) \
                        and _is_stale(path, STALE_INSTANCE_DIR_AGE):
                    os.remove(path)
            else:
                m = _INSTANCE_DIR_RE.match(name)
                if m is None or not os.path.isdir(path):
                    continue
                if int(m.group(1)) == own_pid:
                    continue  # 本进程的任何一代实例目录都不碰（热重载的旧实例可能还活着）
                if current_dir and os.path.abspath(path) == os.path.abspath(current_dir):
                    continue
                if _is_stale(path, STALE_INSTANCE_DIR_AGE):
                    shutil.rmtree(path, ignore_errors=True)
                    reaped += 1
        except OSError as e:
            logger.warning(f"清理残留图片/目录失败（{path}）: {e}")
    if reaped:
        logger.info(f"启动清理：回收了 {reaped} 个过期实例图片目录")

# ---------- 配色（幻星集牌背调亮 + 深色文字，白卡层次） ----------
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
    """tarot_data 牌字典 -> 素材文件绝对路径。"""
    suit = card["suit"]
    n = int(card["num"])
    cn = card["cn"]
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

@functools.lru_cache(maxsize=4)
def _background_cached(suit: str, num: str, cn: str, w: int, h: int) -> Image.Image:
    """牌面 cover 做底 + 深藏青遮罩——背景跟牌走（今日牌运卡与普通拼图共用）。

    cover 放大前先裁中心带：素材上下缘自带牌名/装饰文字，整图放大会把它们
    放大成底部大字；旧海报背景是纯净花纹+人物剪影，取中心 72% 避开。
    素材缺失抛 FileNotFoundError：渲染层 fail loudly，回退由编排层兜底。
    """
    path = _asset_path({"suit": suit, "num": num, "cn": cn})
    if not os.path.exists(path):
        raise FileNotFoundError(f"塔罗素材缺失: {path}")
    src = Image.open(path).convert("RGBA")
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
    return _background_cached(card["suit"], card["num"], card["cn"], w, h).copy()


@functools.lru_cache(maxsize=8)
def _load_card_image(path: str, upright: bool):
    """加载并处理单张牌面：contain 缩放到 INNER_W×INNER_H 内（保持比例，不拉伸）、
    转 RGBA、逆位旋转 180°。返回 (img, w, h)（缩放后实际尺寸，居中用）。

    结果按 (路径, 正逆) 缓存（LRU 8，约 6MB）：这一步是高清素材解码 + LANCZOS 缩放，
    实测本机 50~80ms/张（随负载浮动）——同一张牌再次出现（连续占卜、同群多签）时直接命中。
    返回的图是缓存本体，调用方只许 paste（不得就地改画）。"""
    img: Image.Image = Image.open(path)
    scale = min(INNER_W / img.width, INNER_H / img.height)
    tw = max(1, int(img.width * scale))
    th = max(1, int(img.height * scale))
    img = img.resize((tw, th), _RESAMPLE)
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

    tag = "正位" if upright else "逆位"
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
    canvas = _build_card_background(_RNG.choice(picks)["card"], W, H)
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
        # 生产路径一律显式传实例目录（StarTarot.image_dir，见 new_instance_image_dir）；
        # 这里只是「没有实例上下文」的兜底（离线脚本、单函数测试），
        # 落回根目录的文件由启动清理按过期判据回收
        save_dir = IMAGE_DIR_ROOT
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, f"tarot_{int(time.time())}_{secrets.token_hex(4)}.png")
    canvas.save(path, "PNG")
    return path


# ---------- 今日牌运卡海报 ----------
DAILY_CARD_W = 680                       # 海报宽（竖版）
DAILY_CARD_H = 1200                      # 海报高
DAILY_CARD_Y = 176                       # 白边卡牌顶端 y（其上为标题胶囊与日期）
DAILY_QUOTE_GAP = 68                     # 卡底 → 签文引用块顶端（_text_center 的 y 是文字顶边，不是中心）
# 签文起点由卡片高度推导，勿写死数值：CARD_H 随 PAD_X / INNER_H / INFO_H 变化，
# 定死会让签文与卡片重叠或拉出大空洞（旧版字面量 992 = 176 + 748 + 68）。
# 红线：tests/test_card_render.py::TestDailyCardLayoutInvariants
DAILY_CARD_QUOTE_Y = DAILY_CARD_Y + CARD_H + DAILY_QUOTE_GAP
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
    card_y = DAILY_CARD_Y
    _draw_card(canvas, card_x, card_y, card, upright)

    # 签文引用：24px 常规体（秀气，旧卡同款观感），按标点分行最多两行；
    # 署名与底部水印固定留白
    quote = f"『{signature}』"
    q_font = _load_font(24, text=quote)
    qy = DAILY_CARD_QUOTE_Y
    for line in _split_signature_lines(d, quote, q_font, W - 200)[:2]:
        _text_center(d, W / 2, qy, line, q_font, _DAILY_SIG_TEXT)
        qy += 38
    _text_center(d, W / 2, qy + 12, "—— 牌灵·星羽塔罗",
                 _load_font(23, text="—— 牌灵·星羽塔罗"), _DAILY_SIGN_TEXT)
    _text_center(d, W / 2, H - 58, "星羽塔罗 Star Feather",
                 _load_font(21, text="星羽塔罗 Star Feather"), _DAILY_FOOT_TEXT)

    if save_dir is None:
        # 生产路径一律显式传实例目录（StarTarot.image_dir，见 new_instance_image_dir）；
        # 这里只是「没有实例上下文」的兜底（离线脚本、单函数测试），
        # 落回根目录的文件由启动清理按过期判据回收
        save_dir = IMAGE_DIR_ROOT
    os.makedirs(save_dir, exist_ok=True)
    out_path = os.path.join(save_dir, f"daily_{int(time.time())}_{secrets.token_hex(4)}.png")
    canvas.convert("RGB").save(out_path, "PNG")
    return out_path
