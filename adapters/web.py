"""AstrBot 的 HTTP 请求入口：把宿主 request 代理收窄成两次读取。

`astrbot.api.web.request` 是绑定到当前插件 handler 的上下文代理，只在 handler
内可用。这里刻意保持函数内导入——老版本宿主没有 `astrbot.api.web` 时，插件仍要
能加载；把它写成模块级依赖会让整个 `app` 层跟着导入失败。
"""
from __future__ import annotations

from typing import Any


def query_params() -> Any:
    """本次请求的 query 参数，支持 `.get(key, default)`；只在 handler 内有效。"""
    from astrbot.api.web import request

    return request.query


async def json_body(default: Any = None) -> Any:
    """本次请求的 JSON body；缺失或解析失败时返回 `default`。"""
    from astrbot.api.web import request

    return await request.json(default=default)
