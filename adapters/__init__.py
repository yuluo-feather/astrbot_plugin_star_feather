"""框架适配层：core / services 需要的端口在这里落地成 AstrBot 调用。"""
from .astrbot_io import EventMessenger, PluginKV, messenger_for

__all__ = ["EventMessenger", "PluginKV", "messenger_for"]
