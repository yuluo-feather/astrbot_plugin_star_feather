"""日志出口：直接用框架 logger，落点与插件路由一致（issue #19）。

编排层与纯逻辑层统一从这里取同一个 logger，不各自另起一套日志系统、
也不往 data/logs/ 另开文件；框架是否落文件由宿主决定。
"""
from astrbot.api import logger as log

__all__ = ["log"]
