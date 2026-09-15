"""KV 安全读写工具：任何存储故障都不阻塞占卜主流程。

daily.py（缓存降级）与 gating.py（限流放行）共用的唯一 KV 访问层——
「吞异常 + 打日志」收敛于此，降级策略留在调用点决定，由返回值 ok 驱动：
读失败后是兜底出牌、放行还是重新生成，由调用点自己的业务语义说了算，
这里只保证「读不抛、写不炸」，并把「存储故障」与「无记录」区分开——
gating 需要前者放行且不计数、后者正常计数，搅在一起会改变限流语义。

说人话：门锁坏了别把顾客挡在门外——工具层只负责开门，往哪走你们自己定。

【身世注记】本模块曾因「全仓零调用」被误判为孤儿、差点按删除处置——零引用
静态断言不等于可删：它是对 daily/gating 重复 try-except 的正确抽象；当年接回
线失败的真因是旧接口把「存储故障」与「无记录」折叠成同一个 default，而 gating
的降级策略需要区分二者（故障放行不计数 vs 无记录正常计数）。教训：接口缺陷
往往是断线根因，修接口再接线优于弃模块（详见 modularize 技能 0.6 四查）。
"""
import logging

logger = logging.getLogger(__name__)


async def kv_get(kv, key: str, default=None, what: str = "KV") -> tuple:
    """读 KV，返回 (value, ok)：ok=False 表示存储故障（含对象压根没有
    get_kv_data 方法），此时 value 为 default；ok=True 表示正常读到
    （无记录时 value 为 default，与有值一样属于「正常语义」）。
    数据格式问题（非 dict、非法计数）不在本函数处理范围，
    调用点按自己的策略判定与降级。

    what 是日志上下文（如「每日牌运缓存」「会话节流」），排查时看得清是哪一处降级。
    """
    try:
        return await kv.get_kv_data(key, default), True
    except Exception as e:
        logger.warning(f"{what}读取失败，走调用点降级: {e}")
        return default, False


async def kv_put(kv, key: str, value, what: str = "KV") -> bool:
    """写 KV；任何异常不抛，返回是否写入成功（False 时调用点自行决定降级）。"""
    try:
        await kv.put_kv_data(key, value)
        return True
    except Exception as e:
        logger.warning(f"{what}写入失败: {e}")
        return False
