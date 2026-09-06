"""限流闸门单测：会话节流 / 每日计数 / KV 异常静默放行（gating 粘合层）。
——连刷的念头，掐死在摇篮里。"""
import asyncio
import types

from gating import LimitGate


class FakeKV:
    """内存 KV：dict 语义，模拟 get_kv_data/put_kv_data。"""

    def __init__(self):
        self.store = {}

    async def get_kv_data(self, key, default=None):
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        self.store[key] = value


class BrokenKV:
    """方法存在但存储抛错：闸门必须静默放行。"""

    async def get_kv_data(self, key, default=None):
        raise RuntimeError("kv store down")

    async def put_kv_data(self, key, value):
        raise RuntimeError("kv store down")


class SlowKV:
    """带 IO 延迟的内存 KV：模拟真实 sqlite 的 await 让出点。

    并发测试必需——无让出点的桩（纯 dict 读写）在 gather 下串行完成，
    测不出读-判-写竞态（2026-09-06 渗透实测教训：快桩 30 并发零超发，
    慢桩 30 并发全放行）。"""

    def __init__(self, delay=0.001):
        self.store = {}
        self.delay = delay

    async def get_kv_data(self, key, default=None):
        await asyncio.sleep(self.delay)
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        await asyncio.sleep(self.delay)
        self.store[key] = value


def _evt(uid="u1", origin="g1"):
    e = types.SimpleNamespace(unified_msg_origin=origin)
    e.get_sender_id = lambda: uid
    return e


def _gate(kv=None, cmd=10, daily=0):
    return LimitGate(kv or FakeKV(), cmd, daily)


class TestSessionThrottle:
    def test_disabled_returns_zero_and_no_kv(self):
        kv = FakeKV()
        g = _gate(kv, cmd=0)
        assert asyncio.run(g.session_throttle("sf_tool_cd_x", 0)) == 0
        assert kv.store == {}

    def test_first_call_allows_and_records(self):
        kv = FakeKV()
        g = _gate(kv, cmd=10)
        assert asyncio.run(g.session_throttle("sf_tool_cd_x", 10)) == 0
        assert "sf_tool_cd_x" in kv.store

    def test_within_cooldown_returns_remain(self):
        kv = FakeKV()
        g = _gate(kv, cmd=100)
        asyncio.run(g.session_throttle("sf_tool_cd_x", 100))
        remain = asyncio.run(g.session_throttle("sf_tool_cd_x", 100))
        assert 0 < remain <= 100

    def test_kv_exception_passes(self):
        g = _gate(BrokenKV(), cmd=10)
        assert asyncio.run(g.session_throttle("k", 10)) == 0  # 写失败：放行


class TestGateCheck:
    def test_command_throttle_block_message(self):
        g = _gate(FakeKV(), cmd=100)
        out = asyncio.run(g.check(_evt(), for_command=True))
        assert out is None
        out2 = asyncio.run(g.check(_evt(), for_command=True))
        assert out2 and "歇" in out2 and "秒" in out2

    def test_tool_entry_skips_session_throttle(self):
        # 工具入口 for_command=False：不查会话节流，直接放行（每日计数由别的用例管）
        g = _gate(FakeKV(), cmd=100)
        assert asyncio.run(g.check(_evt(), for_command=False)) is None

    def test_daily_count_exceeded_blocks(self):
        kv = FakeKV()
        g = _gate(kv, cmd=0, daily=1)
        assert asyncio.run(g.check(_evt(uid="u1"), for_command=False)) is None
        out = asyncio.run(g.check(_evt(uid="u1"), for_command=False))
        assert out and "1 次" in out

    def test_daily_disabled_passes(self):
        g = _gate(FakeKV(), cmd=0, daily=0)
        assert asyncio.run(g.check(_evt(), for_command=False)) is None

    def test_uid_missing_skips_daily(self):
        # uid 取不到：跳过每日计数（宁放过不误伤）
        g = _gate(FakeKV(), cmd=0, daily=1)
        assert asyncio.run(g.check(_evt(uid=""), for_command=False)) is None

    def test_broken_kv_passes(self):
        g = _gate(BrokenKV(), cmd=0, daily=5)
        assert asyncio.run(g.check(_evt(), for_command=False)) is None


class TestConcurrentRace:
    """读-判-写原子化（实例级锁）：并发放行不得超配额、会话冷却只放行一次。

    修复前（2026-09-06 渗透实测）：带 IO 延迟桩 30 并发/配额5 全部放行、
    20 并发/冷却10s 全绕过——限流被并发读旧值整体击穿。
    """

    def test_concurrent_daily_count_no_overshoot(self):
        kv = SlowKV()
        g = _gate(kv, cmd=0, daily=5)

        async def fire():
            return await asyncio.gather(
                *[g.check(_evt(uid="u1"), for_command=False) for _ in range(30)])

        outs = asyncio.run(fire())
        allowed = sum(1 for o in outs if o is None)
        assert allowed <= 5, f"并发超发: {allowed}/5"

    def test_concurrent_session_throttle_single_pass(self):
        kv = SlowKV()
        g = _gate(kv, cmd=10)

        async def fire():
            return await asyncio.gather(
                *[g.session_throttle("sf_cmd_cd_g1", 10) for _ in range(20)])

        outs = asyncio.run(fire())
        assert sum(1 for o in outs if o == 0) == 1

    def test_broken_kv_still_passes_with_lock(self):
        """加锁后 KV 故障降级语义不变：读失败静默放行。"""
        g = _gate(BrokenKV(), cmd=0, daily=5)
        assert asyncio.run(g.check(_evt(), for_command=False)) is None
