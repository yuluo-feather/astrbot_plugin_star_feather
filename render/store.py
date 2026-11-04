"""图片产物生命周期：TTL 保险丝、实例目录与启动清理。"""
import asyncio
import itertools
import os
import re
import secrets
import shutil
import tempfile
import time

from ..core.log import log as logger

# ---------- 产物生命周期：渲染出的临时图片由本模块统一管理 ----------
IMAGE_TTL_SECONDS = 600
"""临时图存活上限（秒）：防堆积的保险丝，不是正确性机制。

正确性由「发送那一刻文件还在不在」保证（发送前有 Renderer.ensure_image）；
本常量只管最坏情况下临时目录里堆多少。别调小去省磁盘——省下的空间换不来一次丢消息。
背景与 Issue #2 的经过见 docs/design-notes/image-lifecycle.md。
"""


async def _delayed_remove(path: str, delay: int = IMAGE_TTL_SECONDS) -> None:
    """到点删图（保险丝，见 IMAGE_TTL_SECONDS）：既不耽误发送，也不让临时目录无限堆积。"""
    await asyncio.sleep(delay)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass  # 已被启动清理/其他路径处理，无需管
    except OSError as e:
        # 删不掉（例如文件被别的进程占着）就说出来：退路目录由启动清理回收
        logger.warning(f"临时图片删除失败（{path}），留待启动清理: {e}")


# 清理任务的强引用集合：asyncio 只对任务持弱引用，没人引用时任务可能被 GC 掉，
# 保险丝就静默失效了。任务结束（含取消/异常）由 done callback 自摘。
_CLEANUP_TASKS: set = set()


def _schedule_image_cleanup(img: str | None, delay: int = IMAGE_TTL_SECONDS) -> None:
    """图片产生点即绑定清理，契约是**永不抛**。

    产生点绑定保证 AI 成功、文字兜底、异常中断都被同一条保险丝覆盖；登记失败或
    没有事件循环时只留一张临时图，交给启动清理。入参允许 None / 空串并原地返回。
    背景见 docs/design-notes/image-lifecycle.md。
    """
    if not isinstance(img, str) or not img:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # 无事件循环（测试环境）：不创建协程，避免悬空；启动清理兜底
    coro = _delayed_remove(img, delay)
    try:
        task = asyncio.create_task(coro)
    except Exception as e:
        # 循环已在收尾（AstrBot 热重载 / 进程退出）：get_running_loop 还答得出话，
        # create_task 却抛 RuntimeError('cannot schedule new futures after shutdown')。
        # 先 close 掉刚建出来的协程——不关就是一条「never awaited」告警进日志，
        # 明明只是少个保险丝，别顺手往日志里丢噪声。
        coro.close()
        logger.warning(f"牌面图清理登记失败（保险丝失效，图留待启动清理）: {e}")
        return
    _CLEANUP_TASKS.add(task)
    task.add_done_callback(_CLEANUP_TASKS.discard)


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
而它们恰恰是本案的主角）；随机尾防撞名。pid 必须可解析：清理时按它区分
**本进程的旧实例目录**——只有过期且为空才回收，见 cleanup_stale_images。
"""

STALE_INSTANCE_DIR_AGE = 3 * 24 * 3600
"""目录「可以回收」的年龄判据（秒）：三天。只用来挑磁盘回收候选，不参与正确性。

判据取目录自身的 mtime，即「里面最后一次新增/删除文件距今多久」。它是个**启发式**，
不是「进程已退出」「目录已无人使用」的证明：mtime 只说明「最近三天没往这里写过东西」。
之所以敢据此回收，是因为图片是一次性产物、渲染到发送的间隔以分钟计——一个三天没写过
新东西的目录里，极少还躺着一条正在等发送的图。它撑不住的正确性由另外两条兜住：
同进程的旧实例目录只要非空就免删（见 cleanup_stale_images 的 pid 判据），跨进程那份由
发送前的 Renderer.ensure_image 补渲染兜底。
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
    """根目录不可用时的退路：系统临时目录下的唯一目录（仍然每实例一份）。

    连它都建不出来＝磁盘/权限彻底不可用，返回空串让渲染降级为无图。
    见 docs/design-notes/image-lifecycle.md。
    """
    try:
        return tempfile.mkdtemp(prefix=f"star_feather_inst_{os.getpid()}_")
    except OSError as e:
        logger.warning(f"实例图片目录的临时退路也不可用（{e}）：本实例图片输出降级为无图")
        return ""


_FALLBACK_DIR_PREFIX = "star_feather_inst_"
_FALLBACK_DIR_RE = re.compile(r"^star_feather_inst_(\d+)_")


def _sweep_fallback_dirs(current_dir: str | None = None) -> int:
    """回收系统临时目录下的过期退路实例目录（不在 IMAGE_DIR_ROOT 之下）。

    同样按 STALE_INSTANCE_DIR_AGE 判定；同进程旧目录只收空壳，当前目录永不删。
    返回回收数量。见 docs/design-notes/image-lifecycle.md。
    """
    root = tempfile.gettempdir()
    try:
        names = os.listdir(root)
    except OSError as e:
        logger.warning(f"临时目录枚举失败，跳过退路目录清理: {e}")
        return 0
    own_pid = os.getpid()
    reaped = 0
    for name in names:
        m = _FALLBACK_DIR_RE.match(name)
        if m is None:
            continue
        path = os.path.join(root, name)
        try:
            if not os.path.isdir(path):
                continue
            if current_dir and os.path.abspath(path) == os.path.abspath(current_dir):
                continue
            if not _is_stale(path, STALE_INSTANCE_DIR_AGE):
                continue
            if int(m.group(1)) == own_pid:
                # 同进程旧实例：非空可能还有图在等发送，留给保险丝；空壳才算残骸
                os.rmdir(path)
                reaped += 1
            else:
                shutil.rmtree(path, ignore_errors=True)
                reaped += 1
        except OSError as e:
            logger.warning(f"退路图片目录清理失败（{path}）: {e}")
    return reaped


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
    - **本进程的旧实例目录只回收空壳**——从名字里解析 pid，凡是 os.getpid() 的一律
      先看里面还有没有东西：非空的可能还有图在等发送，宁可留着让保险丝去收；只有
      已经空了的（热重载后旧实例收尾完毕）才按过期回收。热重载正是「同进程、两代
      实例」：新实例启动清理时，旧实例可能还活着，只跳过传入的 current_dir 是不够的
      （旧写法就是这么漏的，issue #21 ① 复核已复现）。删除用 rmdir 而不是 rmtree：
      「非空就删不掉」交给文件系统判断，不靠自己再枚举一次。
    - current_dir 是本次传入的当前实例目录，同样永不删——即便系统时钟被往回调、
      让它的 mtime 看起来已经过期。
    跨进程的过期目录照旧回收（那些 pid 与本进程不同，是重启留下的残骸）。

    与旧实现的关键差别就一句：**旧版清空整个父目录，本版只删判据允许的部分**。
    父目录里不合规的文件（用户或别的程序放的）一律不动：这不是「顺手不删」，
    而是判据的一部分——一个插件没资格替宿主决定哪些文件算垃圾。

    契约：本函数永不抛（启动清理失败不该挡住插件加载），失败只留 warning。
    """
    if not os.path.isdir(IMAGE_DIR_ROOT):
        _sweep_fallback_dirs(current_dir)
        return
    try:
        entries = os.listdir(IMAGE_DIR_ROOT)
    except OSError as e:
        logger.warning(f"图片目录枚举失败，跳过启动清理: {e}")
        # 根目录列不出内容，但退路目录仍在系统临时目录里：照样扫一遍
        _sweep_fallback_dirs(current_dir)
        return

    own_pid = os.getpid()
    reaped = 0
    for name in entries:
        path = os.path.join(IMAGE_DIR_ROOT, name)
        try:
            if name.startswith("tarot_") or name.startswith("daily_"):
                # 旧布局散图（本版起生产路径不再往父目录写图）：同样「确认过期才删」。
                # 后缀收 .png 与 .jpg：前者是旧版本的产物，后者是当前格式。
                if name.endswith((".png", ".jpg")) and os.path.isfile(path) \
                        and _is_stale(path, STALE_INSTANCE_DIR_AGE):
                    os.remove(path)
            else:
                m = _INSTANCE_DIR_RE.match(name)
                if m is None or not os.path.isdir(path):
                    continue
                if current_dir and os.path.abspath(path) == os.path.abspath(current_dir):
                    continue
                if not _is_stale(path, STALE_INSTANCE_DIR_AGE):
                    continue
                if int(m.group(1)) == own_pid:
                    # 同进程的旧实例目录：非空说明可能还有图在等发送，留给保险丝；
                    # 空壳才算残骸。rmdir 在非空时必然失败，正好当判据用。
                    try:
                        os.rmdir(path)
                        reaped += 1
                    except OSError:
                        pass
                else:
                    shutil.rmtree(path, ignore_errors=True)
                    reaped += 1
        except OSError as e:
            logger.warning(f"清理残留图片/目录失败（{path}）: {e}")
    reaped += _sweep_fallback_dirs(current_dir)
    if reaped:
        logger.info(f"启动清理：回收了 {reaped} 个过期实例图片目录")

