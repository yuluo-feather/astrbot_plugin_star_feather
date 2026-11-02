"""日志名与框架插件路由保持一致：服务层不 import astrbot，也能落进同一份日志。"""
import logging

log = logging.getLogger("astrbot.plugin.astrbot_plugin_star_feather")
