"""Golden 回归：重构期间的行为基线。

录制于 2026-11-02（模块拆分之前）。迁移步骤不得改这些输出；确实要改用户可见
行为时，先改这里并写进 changelog，别让重构顺手改掉结果。
"""
import pytest
from data.plugins.astrbot_plugin_star_feather.adapters.deliver import split_sections
from data.plugins.astrbot_plugin_star_feather.ai.prompts import (
    build_reading_prompt,
    build_spirit_line_prompt,
    resolve_persona,
)
from data.plugins.astrbot_plugin_star_feather.core.intent import (
    clean_question,
    is_daily_request,
    norm_topic,
    select_formation,
)
from data.plugins.astrbot_plugin_star_feather.core.tarot_data import TAROT_CARDS
from data.plugins.astrbot_plugin_star_feather.render import (
    _render_daily_card_img,
    render_cards,
)
from PIL import Image

SELECT_GOLDEN = {
    "": "羽时三刻",
    "圣三角 考研如何": "羽镜",
    "恋人十字 我们还有希望吗": "恋羽十字",
    "时间之流 看看明年": "羽时三刻",
    "单张问询 今天": "羽签",
    "我和她感情如何": "恋羽十字",
    "考研 复习计划": "羽镜",
    "下个月运势如何": "羽时三刻",
    "感情 工作": "恋羽十字",
    "他会不会来": "恋羽十字",
    "我们合适吗": "恋羽十字",
    "今天天气不错": "羽时三刻",
    "我想学吉他，前景如何": "羽时三刻",
    "其他方面的运气": "羽时三刻",
    "最近他乡怎么样": "羽时三刻",
    "他人怎么样": "羽时三刻",
    "我喜欢做设计，要不要转行": "羽镜",
    "我该不该表白": "恋羽十字",
    "暗恋一个人很久了": "恋羽十字",
    "我喜欢你，怎么办": "恋羽十字",
    "她会喜欢我吗": "恋羽十字",
    "我和他的过去": "恋羽十字",
    "我们的未来": "恋羽十字",
    "她未来会爱我吗": "恋羽十字",
    "我的未来": "羽时三刻",
    "未来如何": "羽时三刻",
    "过去和未来哪个重要": "羽时三刻",
    "情感过去未来": "恋羽十字",
}

DAILY_GOLDEN = {
    "运势": True,
    "看看今天的运势": True,
    "今天运势": True,
    "今日运势": True,
    "我今天的运气怎么样": True,
    "daily luck": True,
    "本周运势": True,
    "下周运势": True,
    "最近怎么样": True,
    "今天怎么样": True,
    "每日塔罗": True,
    "近期如何": True,
    "这个月如何": True,
    "这两天怎么样": True,
    "这段时间还好吗": True,
    "今天感情运势": True,
    "最近其他事情怎么样": True,
    "今天弹吉他怎么样": True,
    "今天她理我吗": False,
    "最近她对我什么感觉": False,
    "我最近学业怎么样": False,
    "最近我的桃花": False,
    "今天工作如何": False,
    "最近我们的关系": False,
    "这个月考研如何": False,
    "我最近和同事的关系": False,
    "最近的他": False,
    "最近喜欢上了剧本杀": False,
    "今天的桃花运": False,
    "这周怎么样": False,
    "本周如何": False,
    "明天她理我吗": False,
    "他说明天来": False,
    "占卜": False,
    "最近想转行怎么样": False,
    "最近要不要跳槽": False,
    "最近情感怎么样": False,
    "被门夹了，是因为运势太差吗": False,
    "今天运势为什么这么差": True,
}

TOPIC_GOLDEN = {
    "": "（今日牌运）",
    "今天运势": "（今日牌运）",
    "看看今天的运势": "（今日牌运）",
    "每日一签": "（今日牌运）",
    "今天财运怎么样": "今天财运怎么样",
    "今天运势如何，我该不该表白": "今天运势如何，我该不该表白",
    "今天感情运势": "今天感情运势",
    "最近事业运": "最近事业运",
    "我的事业": "我的事业",
    "感情": "感情",
}

CLEAN_GOLDEN = {
    "帮我算一卦我和她的感情": "我和她的感情",
    "麻烦看看我的事业": "我的事业",
    "给我抽一张牌": "",
    "来一卦": "",
    "圣三角 考研如何": "考研如何",
    "请帮我算一卦 我的运势": "我的运势",
    " 我的问题 ": "我的问题",
    "我和她有希望吗": "我和她有希望吗",
}

EXPECTED_READING_PROMPT = (
    "你是一位温柔而专业的塔罗占卜师。\n"
    "--- 用户问题开始 ---\n"
    "我和他还能复合吗\n"
    "--- 用户问题结束 ---\n"
    "牌阵：羽签\n"
    "抽牌结果：\n"
    "- 愚者 正位\n"
    "请按以下格式输出解读：每张牌单独成段，段落开头用【第N张·位置】这样的标记"
    "（例如【第1张·过去】），每段 80~140 字，内容贴合牌位与问题；"
    "牌义方向以抽牌结果的括号关键词为准，顺着它展开，别偏太远、别自创牌义；"
    "最后单独输出一段【总结】，用直白的话一句话说清整体结果与建议（50字左右）。"
    "不要重复用户问题，不要使用 Markdown 表格。格式示例：\n"
    "【第1张·过去】……（本段解读）\n【第2张·现在】……\n【第3张·未来】……\n【总结】……（一句话结论）"
)

EXPECTED_SPIRIT_NEUTRAL = (
    "你是星羽塔罗的牌灵。今天抽到的牌是【愚者·正位】，主题是「今天运势」。"
    "请说一句牌灵的话（25 字以内）：贴合牌面与主题，温柔有画面感。"
    "一句话说人话——比喻必须一读就懂，不玩需要反应的意象；"
    "只输出这句话本身：不加引号、不加解释、不换行。语气中正平和，不刻意搞怪或吓人。"
)

EXPECTED_SPIRIT_TSUNDERE = (
    "你是星羽塔罗的牌灵。今天抽到的牌是【愚者·正位、月亮·逆位】，主题是「我和他的感情」。"
    "请说一句牌灵的话（25 字以内）：贴合牌面与主题，温柔有画面感。"
    "一句话说人话——比喻必须一读就懂，不玩需要反应的意象；"
    "只输出这句话本身：不加引号、不加解释、不换行。"
    "牌灵一句话签文·傲娇格：嘴硬心软，短句带点小别扭（如「哼，就给你看这一眼」），"
    "核心意思一读就懂，不冷嘲热讽。"
)

EXPECTED_SECTIONS = [
    "【第1张·过去】一段过去。",
    "【第2张·现在】一段现在。",
    "【第3张·未来】一段未来。",
    "【总结】结论。",
]

INTERP = ("【第1张·过去】一段过去。\n【第2张·现在】一段现在。\n"
          "【第3张·未来】一段未来。\n【总结】结论。")

RENDER_GOLDEN = {
    "羽签": (412, 986),
    "羽时三刻": (1148, 986),
    "羽镜": (1148, 986),
    "恋羽十字": (780, 1832),
}

_CARDS = {c["cn"]: c for c in TAROT_CARDS}


@pytest.mark.parametrize("text, expected", SELECT_GOLDEN.items())
def test_select_formation_golden(text, expected):
    assert select_formation(text) == expected


@pytest.mark.parametrize("text, expected", DAILY_GOLDEN.items())
def test_is_daily_request_golden(text, expected):
    assert is_daily_request(text) is expected


@pytest.mark.parametrize("text, expected", TOPIC_GOLDEN.items())
def test_norm_topic_golden(text, expected):
    assert norm_topic(text) == expected


@pytest.mark.parametrize("text, expected", CLEAN_GOLDEN.items())
def test_clean_question_golden(text, expected):
    assert clean_question(text) == expected


def test_reading_prompt_golden():
    assert build_reading_prompt("我和他还能复合吗", "羽签", "- 愚者 正位") == \
        EXPECTED_READING_PROMPT


def test_spirit_prompt_golden():
    assert build_spirit_line_prompt([("愚者", True)], "今天运势", None) == \
        EXPECTED_SPIRIT_NEUTRAL
    assert build_spirit_line_prompt([("愚者", True), ("月亮", False)],
                                    "我和他的感情", "tsundere") == \
        EXPECTED_SPIRIT_TSUNDERE


def test_persona_seed_golden():
    assert resolve_persona("random", seed="user-1|20261102") == "mystic"


def test_sections_golden():
    assert split_sections(INTERP, 300) == EXPECTED_SECTIONS


@pytest.mark.parametrize("formation, expected_size", RENDER_GOLDEN.items())
def test_render_size_golden(formation, expected_size, tmp_path):
    from data.plugins.astrbot_plugin_star_feather.core.spreads import FORMATIONS

    positions = FORMATIONS[formation]
    picks = [
        {"card": _CARDS["愚者"], "upright": True},
        {"card": _CARDS["月亮"], "upright": False},
        {"card": _CARDS["太阳"], "upright": True},
        {"card": _CARDS["星星"], "upright": False},
    ][:len(positions)]
    path = render_cards(positions, picks, formation, save_dir=str(tmp_path))
    with Image.open(path) as im:
        assert im.size == expected_size


def test_daily_card_size_golden(tmp_path):
    path = _render_daily_card_img(_CARDS["愚者"], True, "今天适合慢下来。",
                                  "11月2日·周日", save_dir=str(tmp_path))
    with Image.open(path) as im:
        assert im.size == (680, 1200)
