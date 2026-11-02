"""配置新鲜度自证：LOAD / ENTRY 读数与陈旧实例告警。

登记放 context 不放模块全局：热重载会重置模块字典，旧实例读的正是旧模块那份全局，
永远对得上、什么都报不出来。所有诊断读写都包 try：诊断可以失效，插件加载与占卜不能。
"""
import logging

log = logging.getLogger("astrbot.plugin.astrbot_plugin_star_feather")

ACTIVE_INSTANCE_ATTR = "_sf_active_plugin_instance"
CFG_FIELD_NAMES = ("shuffle_lines", "enable_ai", "ai_timeout", "llm_tool_enabled")


def _cfg_target(plugin):
    """优先从装配好的容器读 Settings；只有老形态实例才退回扁平字段。"""
    container = getattr(plugin, "container", None)
    settings = getattr(container, "settings", None)
    if settings is not None:
        return {
            "shuffle_lines": settings.output.shuffle_lines,
            "enable_ai": settings.ai.enable_ai,
            "ai_timeout": settings.ai.timeout,
            "llm_tool_enabled": settings.tool.llm_enabled,
        }, container
    tarot = getattr(plugin, "tarot", None)
    return {name: getattr(tarot, name, "?") for name in CFG_FIELD_NAMES}, tarot


def cfg_fingerprint(plugin) -> str:
    """一行配置读数：两个对象 id 取十六进制 + 四个开关值。"""
    values, target = _cfg_target(plugin)
    fields = " ".join(f"{n}={values[n]!r}" for n in CFG_FIELD_NAMES)
    return f"inst={id(plugin):#x} tarot={id(target):#x} {fields}"


def image_dir_of(plugin) -> str | None:
    """本实例的图片目录（没有容器时退回老形态的 tarot.image_dir）。"""
    container = getattr(plugin, "container", None)
    renderer = getattr(container, "renderer", None)
    path = getattr(renderer, "image_dir", None)
    if not isinstance(path, str) or not path:
        path = getattr(getattr(plugin, "tarot", None), "image_dir", None)
    return path if isinstance(path, str) and path else None


def note_entry_binding(plugin, entry: str) -> None:
    """入口进门自报读数；跑在上一代实例上就当场 warning。永不抛。"""
    try:
        log.info(f"[cfg] ENTRY {entry} {cfg_fingerprint(plugin)}")
        active = getattr(getattr(plugin, "context", None), ACTIVE_INSTANCE_ATTR, None)
        if active is not None and active is not plugin:
            log.warning(
                f"[cfg] 陈旧实例接管了本次 {entry}：inst={id(plugin):#x} "
                f"[{type(plugin).__module__}] 当前代={id(active):#x} "
                f"[{type(active).__module__}] —— 本次读到的很可能是上一代配置，"
                f"与上方 [cfg] LOAD 那行对照即可定罪"
                f"（两侧模块名不同 ⇒ 两份副本，非两代实例）"
            )
    except Exception as e:
        log.warning(f"[cfg] ENTRY {entry} 自检失败（诊断失效，不影响本次）: {e}")


def register_instance(plugin) -> None:
    """initialize 里登记本代实例并打一行 LOAD；失败都只降级。"""
    context = getattr(plugin, "context", None)
    if context is not None:
        try:
            setattr(context, ACTIVE_INSTANCE_ATTR, plugin)
        except Exception as e:
            log.warning(f"[cfg] 实例登记失败（新鲜度自证失效）: {e}")
    try:
        log.info(f"[cfg] LOAD {cfg_fingerprint(plugin)}")
    except Exception as e:
        log.warning(f"[cfg] LOAD 读数行不可用（诊断失效，不影响加载）: {e}")
