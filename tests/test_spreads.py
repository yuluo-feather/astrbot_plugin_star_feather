"""牌阵与问题清洗单测：选阵 / 阵名剥离 / 祈使词剥离（spreads 纯函数）。
——牌灵挑阵的规矩，全在这了。"""
from spreads import clean_tool_question, select_formation, strip_alias


class TestSelectFormation:
    def test_explicit_alias_wins(self):
        assert select_formation("圣三角 考研如何") == "羽镜"
        assert select_formation("恋人十字 我们还有希望吗") == "恋羽十字"
        assert select_formation("时间之流 看看明年") == "羽时三刻"

    def test_keyword_weight(self):
        assert select_formation("我和她感情如何") == "恋羽十字"
        assert select_formation("考研 复习计划") == "羽镜"
        assert select_formation("下个月运势如何") == "羽时三刻"  # 运势词无专属阵，经兜底落羽时三刻

    def test_keyword_tie_keeps_map_order(self):
        # 情感组与事业组各命中 1 词：平局时按 KEYWORD_MAP 顺序取先
        assert select_formation("感情 工作") == "恋羽十字"

    def test_content_inference(self):
        assert select_formation("他会不会来") == "恋羽十字"
        assert select_formation("我们合适吗") == "恋羽十字"

    def test_fallback(self):
        assert select_formation("今天天气不错") == "羽时三刻"
        assert select_formation("") == "羽时三刻"

    def test_timeline_word_with_relation_goes_lovers_cross(self):
        # 时间线词 + 关系语义 = 关系预测，不是纯时间线问题（2026-08-29 边界）
        assert select_formation("我们的未来") == "恋羽十字"
        assert select_formation("她未来会爱我吗") == "恋羽十字"
        assert select_formation("我和他的过去") == "恋羽十字"

    def test_timeline_word_alone_stays_three(self):
        # 无关系主体的时间线问法不变
        assert select_formation("我的未来") == "羽时三刻"
        assert select_formation("未来如何") == "羽时三刻"
        assert select_formation("过去和未来哪个重要") == "羽时三刻"

    def test_blank_is_safe(self):
        assert select_formation(None) == "羽时三刻"


class TestStripAlias:
    def test_standalone_alias_removed(self):
        assert strip_alias("圣三角 考研如何") == "考研如何"

    def test_verb_usage_kept(self):
        # 「单抽一张」中单抽作动词（前为「帮」字非边界），不应剥离
        assert strip_alias("帮我单抽一张牌") == "帮我单抽一张牌"

    def test_standalone_single_removed(self):
        assert strip_alias("单抽") == ""

    def test_punctuation_stripped(self):
        assert strip_alias("圣三角，考研如何？") == "考研如何"

    def test_blank_safe(self):
        assert strip_alias(None) == ""


class TestToolQuestion:
    def test_strip_request_words(self):
        assert clean_tool_question("帮我算一卦我和她的感情") == "我和她的感情"
        assert clean_tool_question("麻烦看看我的事业") == "我的事业"
        assert clean_tool_question("给我抽一张牌") == ""

    def test_strip_alias_in_tool(self):
        assert clean_tool_question("圣三角 考研如何") == "考研如何"

    def test_keep_plain_question(self):
        assert clean_tool_question("我和她有希望吗") == "我和她有希望吗"

    def test_multi_request_words(self):
        assert clean_tool_question("请帮我算一卦 我的运势") == "我的运势"
