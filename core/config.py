"""配置读取工具：分组优先、旧扁平回退、None 兜底、非法值安全转换。

AstrBot 按 _conf_schema.json 的 object 分组存储后，子项落在 group dict 内；
升级前用户改过的扁平配置仍可能残留，两路都读才不会丢设置。
显式兜底：key 存在但值为 None 时同样回 default——None 对配置没有语义，
直接放行会导致 int(None)/bool(None) 转换炸，与「未设置」统一处理。
（说白了：配置这玩意儿，宁可给默认值，也不能让它炸。）
"""

import math
from typing import Any


def _cfg_get(config: dict[str, Any] | None, group: str, key: str, default: Any) -> Any:
    """分组配置读取：优先 group.key（新 schema），回退扁平 key（旧配置兼容）。

    config 为 None 时回默认；两路都未命中（含值为 None）时回 default，绝不返回 None。

    两处 try 形状上冗余（同一个 config 对象、同一个 .get，第一次抛第二次同样抛），
    但刻意保留分开写：分组读取与扁平读取是两条各自容错的路径，合并成一个 try 会把
    「分组读取内部抛错」变成「扁平读取根本不执行」。而 isinstance 已把 grp 锁在 dict 上、
    grp.get 实际不抛，故合不合并行为等价、零收益，不值得为此重验一遍配置读取。
    （2026-10-02 复核：本条已核、非 bug，按追注处理，代码不动。）
    """
    if config is None:
        # 显式短路：给类型收窄用，行为与下面两条 try 的兜底一致
        return default
    try:
        grp = config.get(group)
        if isinstance(grp, dict):
            value = grp.get(key, None)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        value = config.get(key, None)
        if value is not None:
            return value
    except Exception:
        pass
    return default


def _cfg_int(config: dict[str, Any] | None, group: str, key: str, default: int, floor: int | None = None) -> int:
    """int 配置读取：非数字回 default；floor 给定时强制下限（如超时下限 5 秒）。

    数字读取目前只有 int 一种形态（float 配置无需求），不做泛型抽象——
    以后真需要 float 再加回来，YAGNI。
    """
    raw = _cfg_get(config, group, key, default)
    if isinstance(raw, bool):
        # 布尔不是数字：True 当 1 用等于把配置错误悄悄变成有效值
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        # OverflowError：YAML 的 .inf / -.inf 能过 float，却在 int() 处炸；
        # 这里必须兜住，否则 Settings 构造失败会带走整个插件加载
        return default
    return max(floor, value) if floor is not None else value


def _is_negative_number(raw) -> bool:
    """原始配置值是不是负数（含 -0.5 这种取整后变 0 的）。坏值 / 布尔不算。"""
    if isinstance(raw, bool):
        return False
    try:
        num = float(raw)
    except (TypeError, ValueError, OverflowError):
        return False
    # -0.0 带负号但 `num < 0` 为假：要单独看符号位，否则会被 int() 当成显式 0
    return num < 0 or (num == 0 and math.copysign(1.0, num) < 0)


def _is_explicit_zero(raw) -> bool:
    """原始配置值是不是「明确的 0」：数值 0 / 0.0 / 字符串 "0"。

    -0.0 与 0.5 都不算：前者带负号（见 _is_negative_number），后者会被
    int() 截成 0，但配置里写的并不是 0。
    """
    if isinstance(raw, bool):
        return False
    if isinstance(raw, (int, float)):
        return raw == 0 and not _is_negative_number(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("-"):
            return False
        try:
            return int(text) == 0
        except ValueError:
            return False
    return False


def _cfg_int_non_negative(config: dict[str, Any] | None, group: str, key: str, default: int) -> int:
    """非负 int 配置：坏值回 default，只有明确的 0 才表示「关闭」。

    负数、-0.0、以及 0.5 这类截成 0 的正小数都回默认——否则它们会被
    int() 悄悄当成「显式关闭」，让冷却 / 节流 / 每日次数在配置写错时静默失效。
    """
    raw = _cfg_get(config, group, key, default)
    value = _cfg_int(config, group, key, default)
    if value < 0 or (value == 0 and not _is_explicit_zero(raw)):
        return default
    return value


def _cfg_bool(config: dict[str, Any] | None, group: str, key: str, default: bool) -> bool:
    """bool 配置读取：严格解析，吻合 JSON Schema 语义。

    布尔型：原样返回；数值：非 0 为真；字符串：'true/1/yes/on' 为真、
    'false/0/no/off/空' 为假（此前 bool("false") -> True 是错的）；其余回 default。
    """
    raw = _cfg_get(config, group, key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off", ""):
            return False
    return bool(default)
