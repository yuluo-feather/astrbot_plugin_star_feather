"""真框架插件加载观测：按市场安装的目录形状，在真 AstrBot 里 import 一次插件。

补的是单测桩与完整冒烟之间的那一格：
- `tests/test_astrbot_contract.py` 只查框架面还在不在，不导入插件本体；
- `scripts/smoke_startup_check.py` 会起完整 AstrBot（含 WebUI），只在手动触发时跑。
本脚本只做导入期观测，不起服务、不查业务语义。

观测项（现阶段只报不拦，CI 上 continue-on-error）：
  1. 插件入口能否成功导入；
  2. `@register` 是否落进 star_map，版本与 metadata.yaml 是否一致；
  3. handler 注册数量与名字是否与 main.py 的装饰器对得上；
  4. 插件类能否用一个最小上下文实例化。

期望值从 main.py 的 AST 现取（`@filter.command(...)` / `@filter.llm_tool(name=...)`），
不在本脚本里另抄一份名单——抄一份就是第二处真源，早晚漂移。

本机手跑（PYTHONPATH 指到 AstrBot 根）：
    python scripts/plugin_import_check.py
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR_NAME = "astrbot_plugin_star_feather"
PACKAGE = "data.plugins." + PLUGIN_DIR_NAME
_SKIP_DIRS = {"tests", "scripts", "tools", "__pycache__", ".git", ".ruff_cache",
              ".pytest_cache", ".mypy_cache"}


def _report(ok: bool, item: str, detail: str) -> bool:
    print(f"[plugin-load] {'ok  ' if ok else 'FAIL'} {item}: {detail}")
    return ok


def _copy_package(dst_root: Path) -> None:
    """把运行时 .py 与 metadata.yaml 拷进临时目录，还原市场安装的包形状。"""
    dst = dst_root / "data" / "plugins" / PLUGIN_DIR_NAME
    for cur, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            if not (name.endswith(".py") or name == "metadata.yaml"):
                continue
            src = Path(cur) / name
            target = dst / src.relative_to(REPO_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)


def _declared() -> tuple[str, str]:
    """metadata.yaml 的 name / version（取不到返回空串，由测试判红）。"""
    text = (REPO_ROOT / "metadata.yaml").read_text(encoding="utf-8")
    name = re.search(r"^name:\s*[\"']?([^\"'\n]+)", text, re.M)
    version = re.search(r"^version:\s*[\"']?([^\"'\n]+)", text, re.M)
    return (name.group(1).strip() if name else "",
            version.group(1).strip() if version else "")


def _expected_handlers(main_py: Path) -> tuple[set[str], set[str]]:
    """main.py 里声明的命令名与 LLM 工具名（期望值的唯一来源）。"""
    tree = ast.parse(main_py.read_text(encoding="utf-8"), filename=str(main_py))
    commands: set[str] = set()
    tools: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in {"command", "llm_tool"}:
            continue
        if func.attr == "command" and node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, str):
                commands.add(node.args[0].value)
        if func.attr == "llm_tool":
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    tools.add(str(kw.value.value))
    return commands, tools


def _observed_handlers(module_name: str) -> tuple[set[str], set[str]]:
    """真框架注册表里属于本模块的命令名与 LLM 工具名。

    工具名从 `llm_tools.func_list` 取（`@filter.llm_tool(name=...)` 最终落在那里），
    按 handler 的 `__module__` 过滤出本模块的工具——只比数量的话，注册名漂移会被
    当成通过（Codex review, PR #101）。
    """
    from astrbot.core.provider.register import llm_tools
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.star_handler import star_handlers_registry

    commands: set[str] = set()
    for md in star_handlers_registry:
        if md.handler_module_path != module_name:
            continue
        for flt in md.event_filters:
            if isinstance(flt, CommandFilter):
                commands.add(flt.command_name)
    tools = {
        str(tool.name) for tool in llm_tools.func_list
        if getattr(getattr(tool, "handler", None), "__module__", None) == module_name
    }
    return commands, tools


class _MinimalContext:
    """实例化只用得到 get_config：其余框架能力要真跑起来才有，观测不碰。"""

    def get_config(self):
        return {}


def main() -> int:
    declared_name, declared_version = _declared()
    checks = [
        _report(bool(declared_name and declared_version), "metadata",
                f"name={declared_name or '?'} version={declared_version or '?'}"),
    ]

    tmp = Path(tempfile.mkdtemp(prefix="sf-plugin-load-"))
    old_cwd = os.getcwd()
    try:
        _copy_package(tmp)
        sys.path.insert(0, str(tmp))
        # 真框架 import 可能往 cwd 落运行时目录（data/ 之类），别让它落进仓库
        os.chdir(tmp)
        module_name = PACKAGE + ".main"
        try:
            mod = importlib.import_module(module_name)
        except Exception as exc:
            _report(False, "import", f"{type(exc).__name__}: {exc}")
            return 1
        checks.append(_report(True, "import", module_name))

        mod_file = getattr(mod, "__file__", "") or ""
        mod_path = Path(mod_file).resolve() if mod_file else None
        under_tmp = mod_path is not None and mod_path.is_relative_to(tmp.resolve())
        checks.append(_report(under_tmp, "import path",
                              str(mod_path) if mod_path else "<no __file__>"))
        if not under_tmp:
            # 导到别处的同名插件（例如本机运行目录）时，后面的观测都不作数
            return 1

        cls = getattr(mod, "StarFeatherPlugin", None)
        from astrbot.api.star import Star
        if not (isinstance(cls, type) and issubclass(cls, Star)):
            checks.append(_report(False, "plugin class", "StarFeatherPlugin 缺失或未继承 Star"))
            return 1
        checks.append(_report(True, "plugin class", cls.__name__))

        from astrbot.core.star.star import star_map
        md = star_map.get(module_name)
        checks.append(_report(
            md is not None, "register",
            f"name={getattr(md, 'name', '?')} version={getattr(md, 'version', '?')}"))
        checks.append(_report(
            md is not None and md.version == declared_version, "register version",
            f"{getattr(md, 'version', '?')} == metadata {declared_version}"))

        want_cmds, want_tools = _expected_handlers(REPO_ROOT / "main.py")
        got_cmds, got_tools = _observed_handlers(module_name)
        checks.append(_report(
            want_cmds == got_cmds, "commands",
            f"期望 {sorted(want_cmds)}，实际 {sorted(got_cmds)}"))
        checks.append(_report(
            want_tools == got_tools, "llm tools",
            f"期望 {sorted(want_tools)}，实际 {sorted(got_tools)}"))

        try:
            cls(_MinimalContext())
        except Exception as exc:
            checks.append(_report(False, "instantiate", f"{type(exc).__name__}: {exc}"))
        else:
            checks.append(_report(True, "instantiate", "StarFeatherPlugin(context)"))
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for c in checks if c)
    print(f"[plugin-load] {passed}/{len(checks)} 项通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
