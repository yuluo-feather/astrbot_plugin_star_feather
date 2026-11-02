"""同键并发去重：一个键同时只允许一个生成者，其余等待它的结果。

等待者被取消时不会连带取消共享结果；生成者失败/被取消时，等待者会重试一次
（而不是直接拿 fallback，避免把「owner 被取消」误报成 AI 失败）；重试仍失败才回退。
跨事件循环安全：上一个循环留下的死 Future 会被换掉。
"""
import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

_FAILED = object()


class SingleFlight:
    def __init__(self):
        self._inflight: dict[Any, asyncio.Future] = {}
        # 临界区只有同步 dict 操作，用 threading.Lock 避免绑死第一个事件循环
        self._guard = threading.Lock()

    def _claim(self, key):
        loop = asyncio.get_running_loop()
        with self._guard:
            pending = self._inflight.get(key)
            if pending is not None and (pending.done() or pending.get_loop() is not loop):
                pending = None
            owner = pending is None
            if owner:
                pending = loop.create_future()
                self._inflight[key] = pending
        return pending, owner

    def _release(self, key, pending, result) -> None:
        if not pending.done():
            pending.set_result(result)
        with self._guard:
            if self._inflight.get(key) is pending:
                self._inflight.pop(key, None)

    async def run(self, key, factory: Callable[[], Awaitable[Any]],
                  *, fallback: Any = None, _retry: bool = True) -> Any:
        """运行或加入同键生成；owner 返回 factory 结果，等待者返回同一结果。"""
        pending, owner = self._claim(key)
        if not owner:
            try:
                result = await asyncio.shield(pending)
            except asyncio.CancelledError:
                raise
            except Exception:
                return fallback
            if result is _FAILED:
                if _retry:
                    return await self.run(key, factory, fallback=fallback, _retry=False)
                return fallback
            return result
        try:
            result = await factory()
        except BaseException:
            # 唤醒等待者但不给 fallback：它们会看到 _FAILED 并自己重试一次
            self._release(key, pending, _FAILED)
            raise
        self._release(key, pending, result)
        return result
