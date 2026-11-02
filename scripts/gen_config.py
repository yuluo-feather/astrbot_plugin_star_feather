"""配置文档生成/校验入口。

用法（在插件根目录执行）：
    python -m scripts.gen_config --check   # 校验 schema/README 默认值与 Settings 一致
    python -m scripts.gen_config --write   # 按 Settings 回写默认值

注意：本脚本属于开发工具，不参与运行时导入链。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_PKG = "data.plugins.astrbot_plugin_star_feather"


def _mount_plugin_package() -> None:
    """把插件根挂成 AstrBot 的包链，让 `from .config import ...` 成立。

    口径与 tests/conftest.py / tools/build_font_subset.py 一致的最小版；脚本独立跑，
    共用不了测试那份。
    """
    import importlib.machinery
    import types

    parts = PLUGIN_PKG.split(".")
    parent = None
    for i, part in enumerate(parts):
        name = ".".join(parts[:i + 1])
        mod = sys.modules.get(name)
        if mod is None:
            mod = types.ModuleType(name)
            mod.__path__ = [str(ROOT)] if i == len(parts) - 1 else []
            mod.__package__ = name
            mod.__spec__ = importlib.machinery.ModuleSpec(name, None, is_package=True)
            sys.modules[name] = mod
        if parent is not None:
            setattr(parent, part, mod)
        parent = mod


def main(argv=None) -> int:
    args = set(sys.argv[1:] if argv is None else argv)
    import importlib

    try:
        config_docs = importlib.import_module("scripts.config_docs")
    except ModuleNotFoundError:  # 直接 python scripts/gen_config.py 时
        config_docs = importlib.import_module("config_docs")
    _mount_plugin_package()
    from data.plugins.astrbot_plugin_star_feather.settings import parse

    if "--write" in args:
        changed = config_docs.write(parse, ROOT)
        print(f"已回写 {len(changed)} 处：")
        for item in changed:
            print(" -", item)
        return 0
    problems = config_docs.check(parse, ROOT)
    if problems:
        print("配置默认值与 Settings 不一致：")
        for item in problems:
            print(" -", item)
        return 1
    print("配置默认值一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
