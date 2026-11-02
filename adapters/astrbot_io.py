"""AstrBot 适配：KV 与消息出口。

纯逻辑与编排层不 import astrbot；需要框架能力时从这里取实现。
"""
from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain


class PluginKV:
    """插件 KV 的端口实现。

    同时保留 get_kv_data / put_kv_data：kv_utils 与 gating/daily 仍按这两个方法
    读写的旧调用点不用跟着改。get / put 是对外端口，负责把存储故障转成 ok 标志。
    """

    def __init__(self, store):
        # 必须传插件实例自身（Star 基类混入 PluginKVStoreMixin 提供 get/put_kv_data）
        self._store = store

    async def get_kv_data(self, key, default=None):
        return await self._store.get_kv_data(key, default)

    async def put_kv_data(self, key, value):
        await self._store.put_kv_data(key, value)

    async def delete_kv_data(self, key):
        await self._store.delete_kv_data(key)

    async def get(self, key: str, default=None) -> tuple:
        try:
            return await self._store.get_kv_data(key, default), True
        except Exception as e:
            logger.warning(f"KV 读取失败（{key}），走调用点降级: {e}")
            return default, False

    async def put(self, key: str, value) -> bool:
        try:
            await self._store.put_kv_data(key, value)
            return True
        except Exception as e:
            logger.warning(f"KV 写入失败（{key}）: {e}")
            return False


class EventMessenger:
    """事件消息出口：文本直发与结果对象直发。

    命令入口与工具入口都走 event.send：yield 的结果要等 handler 结束后的响应阶段
    才发送，收尾句的次序就变成了赌窗口。直发让「结果先、收尾后」成为确定顺序。
    """

    def __init__(self, event):
        self.event = event

    async def send_text(self, text: str) -> bool:
        try:
            await self.event.send(MessageChain([Plain(text)]))
            return True
        except Exception as e:
            logger.warning(f"文本发送失败: {e}")
            return False

    async def send_result(self, result) -> bool:
        try:
            await self.event.send(result)
            return True
        except Exception as e:
            logger.warning(f"结果发送失败: {e}")
            return False


def messenger_for(event) -> EventMessenger:
    """给一个事件套上消息端口。"""
    return EventMessenger(event)
