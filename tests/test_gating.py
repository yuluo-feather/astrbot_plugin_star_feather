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


class MeteredSlowKV(SlowKV):
    """带 IO 延迟的 KV，顺手量「同时在读写的人数」。

    判的是 gating 的读-判-写窗口有没有被并发闯进来：锁真的罩住整段窗口时，任何
    时刻只有一个协程在做 KV IO（peak == 1）；锁被摘掉或挪到 KV 读写之外，这里立刻
    涨到并发数量级。比「放行数没超配额」更贴机制——配额不超也可能出于巧合。
    """

    def __init__(self, delay=0.002):
        super().__init__(delay)
        self.inflight = 0
        self.peak = 0

    async def _io_pause(self):
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        await asyncio.sleep(self.delay)     # 让出点：没锁的话别人正是从这里挤进来
        self.inflight -= 1

    async def get_kv_data(self, key, default=None):
        await self._io_pause()
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        await self._io_pause()
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


    def test_bad_timestamp_treated_as_no_record(self):
        """KV 里是坏时间戳（字符串/对象）时当「没有记录」放行。

        float() 直接抛出去的话，命令入口会落进「这场占卜断了」的兜底文案、工具
        入口会走「这卦起得有点乱」，把用户的正常请求误伤成故障。
        """
        kv = FakeKV()
        kv.store["k"] = "not-a-timestamp"
        g = _gate(kv, cmd=100)
        assert asyncio.run(g.session_throttle("k", 100)) == 0

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

    def test_quota_exact_and_kv_window_serialized(self):
        """配额并发风暴：放行数恰好等于配额、落库计数同步、读写窗口始终只有一个人。

        只量 gating.py 自己的读-判-写，三条断言钉三件事：
        1) 不超发（同时放行 30 个请求，配额 5 → 恰好 5，多了就是竞态放行）；
        2) 不少发（同样恰好 5，少了说明闸门自己把正常请求挡了）；
        3) 窗口没被闯进来（peak == 1；锁一旦没罩住读写，这里当场爆）。
        """
        kv = MeteredSlowKV(delay=0.002)
        g = _gate(kv, cmd=0, daily=5)

        async def fire():
            return await asyncio.gather(
                *[g.check(_evt(uid="u1"), for_command=False) for _ in range(30)])

        outs = asyncio.run(fire())
        allowed = sum(1 for o in outs if o is None)
        assert allowed == 5, f"放行数不恰好等于配额: {allowed}/5"
        assert kv.store["sf_cmd_cnt_u1"]["count"] == 5, \
            "落库计数与放行数不一致：" + str(kv.store.get("sf_cmd_cnt_u1"))
        assert kv.peak == 1, f"读-判-写窗口被并发闯入: 同时 {kv.peak} 个人在做 KV IO"

    def test_broken_kv_still_passes_with_lock(self):
        """加锁后 KV 故障降级语义不变：读失败静默放行。"""
        g = _gate(BrokenKV(), cmd=0, daily=5)
        assert asyncio.run(g.check(_evt(), for_command=False)) is None
