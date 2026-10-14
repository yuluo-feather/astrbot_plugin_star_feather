"""导入模型红线：插件模块只有一个身份（包名），插件自己不再备导入机关。

2026-10-12 把插件从「顶层平铺名 + 手动清 sys.modules」换成包内相对导入
（from .daily import ...）。收益靠这三条守住——别处的用例全绿替代不了它们：

1. 一份文件一个身份：插件目录下的模块只能注册成 data.plugins.<目录名>.*。
   老形态下同一个 daily.py 会是「顶层 daily」＋「data.plugins....daily」两份，
   monkeypatch 打在哪个上全看运气，打空了照样绿。
2. 插件根没有 sys 机关：插件目录里的模块不访问 sys.path / sys.modules。
   那是框架的活；插件自己动手就会歪掉上一条，还连累同一解释器里同名的别人的模块。
3. 框架清得干净：按 data.plugins.<目录名>.* 前缀删，插件模块一个不剩、再导入拿到
   全新对象。顶层名永远匹配不到这个前缀——这正是当初 main.py 非写一段 pop 循环
   不可的原因。
"""
import ast
import os
import subprocess
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_TESTS_DIR)
_PLUGIN_PKG = "data.plugins.astrbot_plugin_star_feather"


def _plugin_sources():
    for name in sorted(os.listdir(_PLUGIN_ROOT)):
        if name.endswith(".py"):
            yield name, os.path.join(_PLUGIN_ROOT, name)


class TestOneIdentityPerFile:
    def test_plugin_modules_only_registered_under_package_name(self):
        """插件目录下的模块只许有包名这一个身份（冒出顶层名 = 老形态回来了）。"""
        from data.plugins.astrbot_plugin_star_feather import main as main_mod

        assert main_mod is sys.modules[_PLUGIN_PKG + ".main"], "入口没按包名注册？"
        offenders = []
        for name, mod in list(sys.modules.items()):
            path = getattr(mod, "__file__", None)
            if not path or os.path.dirname(os.path.abspath(path)) != _PLUGIN_ROOT:
                continue
            if name == _PLUGIN_PKG or name.startswith(_PLUGIN_PKG + "."):
                continue
            offenders.append(name)
        assert not offenders, (
            "插件模块出现了第二个身份（顶层名 + 包名）：" + repr(sorted(offenders))
            + "——补丁会打在没包名的那份上，静默打空"
        )


class TestNoImportMachineryInPlugin:
    def test_plugin_root_touches_no_sys_path_or_sys_modules(self):
        """扫描 AST（注释与字符串里提到不算），逐处报文件与行号。"""
        hits = []
        for fname, fpath in _plugin_sources():
            with open(fpath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fname)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id == "sys" and node.attr in ("path", "modules")):
                    hits.append(f"{fname}:{node.lineno} sys.{node.attr}")
        assert not hits, "插件根不该自备导入机关：" + "；".join(hits)


_CHILD = r'''
import importlib
import sys

sys.path.insert(0, r"{tests_dir}")
import conftest  # noqa: F401  装上 astrbot 桩与插件包链

pkg = "{pkg}"
main1 = importlib.import_module(pkg + ".main")
core1 = importlib.import_module(pkg + ".tarot_core")
tracked = sorted(n for n in sys.modules if n.startswith(pkg + "."))
assert len(tracked) >= 10, "插件模块没挂在包名下：" + repr(tracked)
assert pkg + ".daily" in tracked and pkg + ".prompts" in tracked, str(tracked)

# 模拟框架的重载清理：按 data.plugins.<目录名>.* 前缀删（AstrBot 的清理就是这个口径）
for name in tracked:
    del sys.modules[name]
left = sorted(n for n in sys.modules if n.startswith(pkg + "."))
assert left == [], "前缀清理漏了：" + repr(left)

main2 = importlib.import_module(pkg + ".main")
core2 = importlib.import_module(pkg + ".tarot_core")
card2 = importlib.import_module(pkg + ".card_render")
assert main2 is not main1 and core2 is not core1, "重新导入拿到的是旧模块（僵尸没清掉）"
# 同源自检：tarot_core 的模块级名字引用的必须是新 card_render 里的那个函数
assert core2._schedule_image_cleanup.__globals__ is card2.__dict__, "两模块不是同一代"
print("OK:" + str(len(tracked)))
'''


class TestFrameworkPurgeReachesEverything:
    def test_prefix_purge_clears_all_and_reimport_is_fresh(self):
        """在子进程里真删真重导：本进程还握着别的地方的模块对象，不能就地糟蹋。"""
        code = _CHILD.format(tests_dir=_TESTS_DIR, pkg=_PLUGIN_PKG)
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=_TESTS_DIR)
        assert proc.returncode == 0, (
            "子进程导入模型自证失败：\n" + proc.stdout + "\n" + proc.stderr
        )
        assert proc.stdout.startswith("OK:"), proc.stdout
