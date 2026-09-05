"""运行日志落盘：AstrBot 主进程日志只走控制台/WebUI，磁盘上没有。

这里把关键运行事件（AI 解读剥除命中/剥空/结构失格/候选链切换/冷却）写入
data/logs/star_feather.log，方便排障与观察注入防护效果。

与 interpret 解耦：日志是「运行诊断」域，AI 客户端只负责调模型。

——牌灵做过什么，这里都留一底账，出状况时翻它。
"""
import logging
import os
import tempfile
from logging.handlers import TimedRotatingFileHandler

_HANDLER_FLAG = "_sf_log_handler"


def resolve_log_path() -> str:
    """日志路径：不依赖安装位置（data/plugins 或 site-packages 均可用）。

    候选顺序：AstrBot 数据目录 logs/（data/plugins 安装时）、插件包内 logs/、
    系统临时目录。取第一个可创建者；全部失败时回退临时目录（写不进去就算了）。
    """
    pkg_root = os.path.dirname(os.path.abspath(__file__))  # 插件包根目录
    candidates = (
        os.path.join(os.path.dirname(os.path.dirname(pkg_root)), "logs"),
        os.path.join(pkg_root, "logs"),
        os.path.join(tempfile.gettempdir(), "star_feather_logs"),
    )
    for base in candidates:
        try:
            os.makedirs(base, exist_ok=True)
            return os.path.join(base, "star_feather.log")
        except Exception:
            continue
    return "star_feather.log"


def setup_logging(logger: logging.Logger) -> bool:
    """给 logger 安装每日轮转文件 handler（防重复安装），返回是否成功。

    日志文件不可用不影响主流程（小事，别慌）；重复调用只装一次
    （按 handler 上的标记判定，插件热重载安全）。
    """
    try:
        if not any(getattr(h, _HANDLER_FLAG, False) for h in logger.handlers):
            fh = TimedRotatingFileHandler(resolve_log_path(), when="midnight",
                                          backupCount=7, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
            fh._sf_log_handler = True
            logger.addHandler(fh)
            logger.setLevel(logging.INFO)
        return True
    except Exception as exc:  # 日志文件不可用不影响解读主流程
        logger.warning(f"star_feather.log 初始化失败: {exc}")
        return False
