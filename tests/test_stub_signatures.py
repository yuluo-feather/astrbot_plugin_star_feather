"""打桩签名一致性（AST 静态检查）：monkeypatch 的 fake 必须「接得下」真实签名。

为什么要有：测试里 monkeypatch 打的桩若签名比真实函数窄（或打错模块），
测试照常全绿、问题潜伏到线上。本检查把「fake 能接下的参数个数 ≥ 真实参数个数」
机械化。

管什么（防假绿）：
- 只查签名宽度：fake 带 *args / **kwargs 一律通过（能接下任意调用）；
  否则 fake 的位置参数个数须 ≥ 真实对象的可位置传参个数。
- 真实对象从实际模块取（importlib 导入后 getattr），取不到就跳过，不误报；
  真实对象是类时用 inspect.signature 取到的是去掉 self 的 __init__ 签名。
- fake 只认「本文件里定义的函数 / async 函数 / lambda」，取不到参数就跳过。

不管什么（本检查的边界，别指望它）：
- **不查「打桩是否打在调用点读取的模块上」**。`from m import f` 之后调用点读的是
  哪个绑定，AST 看不出来，只能人眼确认。真实案例（2026-09-10 修）：
  test_core 曾写 monkeypatch.setattr("card_render._schedule_image_cleanup",
  lambda img: None)——被测路径 tarot_core._maybe_render_image 用的是
  tarot_core 里 `from card_render import _schedule_image_cleanup` 绑定的那份引用，
  打 card_render 的模块属性对调用点无效（真实函数照跑，还挂了延迟删除任务），
  且 fake 少了 delay 参数。签名宽度由本检查拦下；打错模块请改打在被测模块的
  名字上（如 monkeypatch.setattr(tarot_core, "_schedule_image_cleanup", ...)）。
- 不查语义：fake 返回值对不对、是否被调用，那是各自用例的断言。
- **不查实例属性打桩，也不查给自制桩对象添方法**。前者（`t._ai_interpret = fake`）
  是替换真实方法但有签名风险，只是实例属性不在模块命名空间、AST 无从解析真实签名；
  后者（`e.chain_result = lambda ...`）根本没有真实签名可对。这两类只能靠运行时
  `TypeError` 兜底——那意味着「fake 比真函数窄、而调用点不传那个可选参」时会静默。
  所以模块属性那一类（`monkeypatch.setattr(module, "f", fake)`）务必用它，别写成
  直接赋值。
"""
import ast
import importlib
import inspect
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _module_bindings(tree) -> dict:
    """本文件的模块名绑定：{局部名: 可导入名字}。

    `import daily` → daily；`import daily as d` → daily；`from x import y`
    记录成 "x.y"（只有 y 真是模块时才导得进来，否则解析时自然跳过）。
    相对导入不涉及时模块名，跳过。
    """
    binds = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                binds[local] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:
                for alias in node.names:
                    binds[alias.asname or alias.name] = node.module + "." + alias.name
    return binds


def _attr_chain(expr):
    """AST 表达式 → (根名字, [属性...])；不是「名字 + 属性链」就返回 None。"""
    parts = []
    node = expr
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return node.id, list(reversed(parts))


def _resolve_target(target, attr, binds):
    """把打桩点解析成真实对象；解析不了返回 None（跳过，不误报）。

    两种写法：setattr("<mod>.<name>", fake)（attr 为 None，名字在字符串里）
    与 setattr(<mod_obj>, "<name>", fake)（mod_obj 顺着本文件 import 解析成模块，
    如 card_render.random → random.choice 那颗真实 choice）。
    """
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        parts = target.value.split(".")
        for i in range(len(parts) - 1, 0, -1):   # 找可导入的最长前缀
            mod = _import(".".join(parts[:i]))
            if mod is None:
                continue
            obj = mod
            for name in parts[i:]:
                obj = getattr(obj, name, None)
                if obj is None:
                    return None
            return obj
        return None
    if attr is None:
        return None
    chain = _attr_chain(target)
    if chain is None:
        return None
    root, attrs = chain
    bind = binds.get(root)          # 根名字必须是本文件导入的模块；局部对象（被测实例）跳过
    if bind is None:
        return None
    obj = _import(bind)
    if obj is None:
        return None
    for name in attrs:
        obj = getattr(obj, name, None)
        if obj is None:
            return None
    return getattr(obj, attr, None)   # 模块或模块属性（如 card_render.random）上的被桩对象


def _func_defs(tree) -> dict:
    """本文件里的函数定义：{名字: (ast.arguments, 是否方法)}（同名取第一个）。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, (node.args, False))
    # 直接挂在类体里的函数算方法：首参 self/cls 是调用方不传的
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for sub in cls.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[sub.name] = (sub.args, True)
    return out


def _fake_width(fake, defs):
    """fake 能接受的位置参数个数；含 *args/**kwargs 返回 None（表示「随便传」）。"""
    is_method = False
    if isinstance(fake, ast.Lambda):
        args = fake.args
    elif isinstance(fake, ast.Name) and fake.id in defs:
        args, is_method = defs[fake.id]
    else:
        return "unknown"          # 调用表达式 / 非本文件函数：跳过
    if args.vararg or args.kwarg:
        return None               # *args / **kwargs：能接下任意调用
    named = list(args.posonlyargs) + list(args.args)
    if is_method and named:
        named = named[1:]         # self / cls
    return len(named)


def _real_width(obj):
    """真实对象的可位置传参个数；取不到签名返回 None（跳过）。"""
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return None
    if not callable(obj):
        return None
    return sum(1 for p in sig.parameters.values()
               if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))


def _sites():
    """扫描 tests/*.py 里的 monkeypatch.setattr 打桩点。

    返回 (已检查列表, 违规列表, 跳过列表)：每项是可读的说明字符串。
    """
    checked, violations, skipped = [], [], []
    for name in sorted(os.listdir(_TESTS_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_TESTS_DIR, name)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        binds = _module_bindings(tree)
        defs = _func_defs(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setattr"):
                continue
            if len(node.args) == 2:            # setattr("<mod>.<name>", fake)
                target_arg, attr_arg, fake_arg = node.args[0], None, node.args[1]
            elif len(node.args) == 3:          # setattr(<mod>, "<name>", fake)
                target_arg, attr_arg, fake_arg = node.args[0], node.args[1], node.args[2]
            else:
                continue
            where = name + ":" + str(node.lineno)
            attr = attr_arg.value if isinstance(attr_arg, ast.Constant) \
                and isinstance(attr_arg.value, str) else None
            real = _resolve_target(target_arg, attr, binds)
            if real is None:
                skipped.append(where + " 目标非模块属性/解析不到")
                continue
            rw = _real_width(real)
            if rw is None:
                skipped.append(where + " 真实对象无签名/非可调用")
                continue
            fw = _fake_width(fake_arg, defs)
            if fw == "unknown":
                skipped.append(where + " fake 不是本文件函数/lambda")
                continue
            target = ast.unparse(target_arg) + ("." + attr if attr else "")
            if fw is None:
                checked.append(where + " " + target + "：fake 带 */**，随便传")
                continue
            detail = (where + " " + target + "：fake 参数 " + str(fw)
                      + " >= 真实参数 " + str(rw))
            if fw < rw:
                violations.append(detail)
            else:
                checked.append(detail + "（OK）")
    return checked, violations, skipped


def test_stub_fake_signatures_are_wide_enough():
    """所有可解析的打桩点：fake 参数个数不得少于真实签名。"""
    checked, violations, skipped = _sites()
    assert checked, ("没扫到任何可解析的打桩点——检查本身失效了，别当绿灯；"
                     "跳过项：" + "；".join(skipped))
    assert not violations, "打桩签名比真实签名窄（fake 接不下真实调用）：" + "\n".join(violations)


def test_scanner_skips_unresolvable_targets_without_crashing():
    """解析不到的目标（局部对象、非函数 fake）进跳过名单，不误报也不炸。"""
    checked, _violations, skipped = _sites()
    assert checked or skipped


if __name__ == "__main__":          # 手工跑：打印逐条结论，便于定位
    import sys
    sys.path.insert(0, os.path.dirname(_TESTS_DIR))
    import conftest  # noqa: F401  装上 astrbot 桩，插件模块才导得进来
    c, v, s = _sites()
    print("已检查 " + str(len(c)) + " 处，违规 " + str(len(v)) + " 处，跳过 " + str(len(s)) + " 处")
    for line in c:
        print("  [OK]  " + line)
    for line in s:
        print("  [跳过] " + line)
    for line in v:
        print("  [违规] " + line)
