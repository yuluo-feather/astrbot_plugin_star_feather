"""测试清单对账：README.md / README.en.md / tests/conftest.py 三处手写清单
与 tests/ 目录的实际 test_*.py 文件集合必须**相等**（多一个少一个都要报）。

为什么：手写清单没有对账就是过期文档——README 漏了 test_kv_utils、
conftest 头注释漏了 test_integrity，两边各自腐化，读文档的人拿到的是假清单。
把 parse 出来的名字与实际文件集合直接比，腐化当场红。

边界：CHANGELOG 不参与对账（历史条目记录当时状态，是对的）；清单里的模块名
形态固定为 test_ 开头的蛇形名，注释性提及（如「踩过：test_dailylines 曾全路径
导入」）不在清单区段内，不会误收。解析容错：markdown 反引号、中英混排、跨行
断句都认；解析不到任何名字直接失败，防止解析失效把断言空转成假绿。
"""
import os
import re

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.dirname(_TESTS_DIR)

# 清单锚点：清单紧跟在锚点之后（改文案要同步改这里）
ANCHORS = {
    "README.md": "纯逻辑单测位于",
    "README.en.md": "Pure-logic unit tests in",
    os.path.join("tests", "conftest.py"): "测试按域分文件",
}
# 模块名形态：test_ 开头 + 小写蛇形；左右不能再接标识符字符（别从 pytest /
# xtest_foo 里刨出半截名字）
NAME_RE = re.compile(r"(?<![A-Za-z0-9_])test_[a-z][a-z_]*")


def _list_text(rel_path: str) -> str:
    """取清单所在文本片段：md 取锚点那一行；conftest 取锚点到第一个句号（跨行断句）。"""
    with open(os.path.join(_PLUGIN_ROOT, rel_path), encoding="utf-8") as f:
        text = f.read()
    anchor = ANCHORS[rel_path]
    i = text.find(anchor)
    assert i >= 0, rel_path + " 找不到清单锚点 " + repr(anchor) + "（锚点变了要同步改本测试）"
    seg = text[i:]
    if rel_path.endswith(".md"):
        return seg.split("\n", 1)[0]
    end = seg.find("。")
    return seg[:end] if end >= 0 else seg


def _listed_names(rel_path: str) -> set:
    return set(NAME_RE.findall(_list_text(rel_path)))


def _actual_names() -> set:
    return {n[:-3] for n in os.listdir(_TESTS_DIR)
            if n.startswith("test_") and n.endswith(".py")}


def test_list_anchor_parsable():
    """三处清单都要解析得到模块名（解析失效=对账空转，直接红）。"""
    actual = _actual_names()
    assert actual, "tests/ 下没有 test_*.py？路径不对"
    for rel in ANCHORS:
        names = _listed_names(rel)
        assert names, rel + " 的清单解析不到任何模块名（解析失效会让对账空转）"


def test_lists_match_tests_dir():
    """三处清单与实际 tests/ 文件集合集合相等（漏项 / 多余项都要报出来）。"""
    actual = _actual_names()
    problems = []
    for rel in ANCHORS:
        listed = _listed_names(rel)
        missing = sorted(actual - listed)
        extra = sorted(listed - actual)
        if missing or extra:
            problems.append(rel + ": 漏掉 " + str(missing) + "；多余 " + str(extra))
    assert not problems, "测试清单与实际 tests/ 不一致：" + "；".join(problems)
