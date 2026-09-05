"""kv_utils 单测：读不抛 / 写不炸 / 故障与无记录可区分。
——门锁坏了别把顾客挡在门外；但「门开着」和「屋里没人」得让掌柜分得清。
"""
import asyncio

from kv_utils import kv_get, kv_put


class MemoryKV:
    """内存 KV：get 无记录返回 default，put 写入。"""

    def __init__(self, store=None):
        self.store = store or {}

    async def get_kv_data(self, key, default=None):
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        self.store[key] = value


class BrokenKV:
    """方法存在但存储抛错：工具层必须不抛，把选择权交给调用点。"""

    async def get_kv_data(self, key, default=None):
        raise RuntimeError("kv store down")

    async def put_kv_data(self, key, value):
        raise RuntimeError("kv store down")


def test_missing_returns_default_with_ok():
    """无记录：返回 default 且 ok=True（正常语义，调用点可继续按「无缓存」处理）。"""
    val, ok = asyncio.run(kv_get(MemoryKV(), "k", None))
    assert val is None and ok is True


def test_found_returns_value_with_ok():
    """有记录：值原样返回，ok=True。"""
    val, ok = asyncio.run(kv_get(MemoryKV({"k": {"a": 1}}), "k"))
    assert val == {"a": 1} and ok is True


def test_broken_store_returns_default_not_ok():
    """存储故障：不抛异常，返回 default 且 ok=False——调用点据此放行不计数。"""
    val, ok = asyncio.run(kv_get(BrokenKV(), "k", 0))
    assert val == 0 and ok is False


def test_missing_kv_api_returns_not_ok():
    """接口缺失（真实 AstrBot Context 没有 get_kv_data）：同样不抛、ok=False。"""
    val, ok = asyncio.run(kv_get(object(), "k", {}))
    assert val == {} and ok is False


def test_put_success_returns_true():
    kv = MemoryKV()
    assert asyncio.run(kv_put(kv, "k", {"x": 1})) is True
    assert kv.store["k"] == {"x": 1}


def test_put_broken_returns_false():
    assert asyncio.run(kv_put(BrokenKV(), "k", 1)) is False


def test_log_context_in_warning(caplog):
    """日志上下文 what 出现在 warning 里——排查时知道是哪一处降级。"""
    with caplog.at_level("WARNING", logger="kv_utils"):
        asyncio.run(kv_get(BrokenKV(), "k", None, "每日牌运缓存"))
        assert "每日牌运缓存" in caplog.text
