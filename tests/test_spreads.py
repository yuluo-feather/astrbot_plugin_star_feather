"""意图判定单测：选阵 / 阵名剥离 / 祈使词剥离（intent 纯函数）。
——牌灵挑阵的规矩，全在这了。"""
from data.plugins.astrbot_plugin_star_feather.intent import (
    build_request,
    clean_question,
    select_formation,
    strip_alias,
)


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

    def test_pronoun_substring_is_not_relation(self):
        """单字「他」的构词面不是关系主体：「其他／吉他／他乡」不得判成恋爱阵。

        2026-11-01 复核：子串匹配下这三类问题全被选成恋羽十字。
        「其他方面的运气」在默认配置下先命中今日牌运（走不到选阵层），
        这里直接测选阵函数本身的口径。
        """
        assert select_formation("我想学吉他，前景如何") == "羽时三刻"
        assert select_formation("其他方面的运气") == "羽时三刻"
        assert select_formation("最近他乡怎么样") == "羽时三刻"
        assert select_formation("他人怎么样") == "羽时三刻"

    def test_hobby_like_is_not_romance(self):
        """「喜欢」不再单独作为恋爱关键词：爱好类问题不判恋爱阵。

        「转行」归事业组后这类问法选羽镜（2026-11-02），关键是别再落到恋爱阵。
        """
        assert select_formation("我喜欢做设计，要不要转行") == "羽镜"

    def test_romance_phrases_without_pronoun(self):
        """没有「他/她」时，爱慕短语也要进恋爱阵（review 复核）。"""
        assert select_formation("我该不该表白") == "恋羽十字"
        assert select_formation("暗恋一个人很久了") == "恋羽十字"
        assert select_formation("我喜欢你，怎么办") == "恋羽十字"

    def test_pronoun_relation_still_lovers(self):
        """摘构词的同时不能把真有关系主体的问题一起摘掉。"""
        assert select_formation("她会喜欢我吗") == "恋羽十字"
        assert select_formation("我和他的过去") == "恋羽十字"

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
        assert clean_question("帮我算一卦我和她的感情") == "我和她的感情"
        assert clean_question("麻烦看看我的事业") == "我的事业"
        assert clean_question("给我抽一张牌") == ""
        assert clean_question("来一卦") == ""      # 长词优先：别剥成「一卦」

    def test_strip_alias_in_tool(self):
        assert clean_question("圣三角 考研如何") == "考研如何"

    def test_keep_plain_question(self):
        assert clean_question("我和她有希望吗") == "我和她有希望吗"

    def test_multi_request_words(self):
        assert clean_question("请帮我算一卦 我的运势") == "我的运势"


class TestRelationWordsDerived:
    """RELATION_WORDS 必须**引用** KEYWORD_MAP 感情组，不能抄一份字面量。

    抄过的那份漏了「情感」：当时间线词 ≥2 且句中没有他/她/我们时（「情感过去未来」），
    情感组得分落后于时间线组 → best=羽时三刻 → select_formation 第 71 行的升级判定
    因「情感」不在 RELATION_WORDS 而失效 → 关系问题被当成纯时间线问题（"感情过去未来"
    却是对的，同一个语义、两条路径不一致）。
    """

    def test_relation_words_covers_keyword_map_emotion_group(self):
        from data.plugins.astrbot_plugin_star_feather.intent import (
            KEYWORD_MAP,
            RELATION_WORDS,
        )
        missing = [w for w in KEYWORD_MAP[0][0] if w not in RELATION_WORDS]
        assert not missing, f"KEYWORD_MAP 情感组有词没进 RELATION_WORDS：{missing}"

    def test_emotion_plus_timeline_upgrades_to_relation_spread(self):
        from data.plugins.astrbot_plugin_star_feather.intent import select_formation
        # 唯一能暴露漏词的形状：时间线词 2 个 > 情感词 1 个，且不含「他/她/我们」
        assert select_formation("情感过去未来") == "恋羽十字"
        assert select_formation("情感未来时间线") == "恋羽十字"
        # 对照：同一语义的另一条路径，改前就对，改后必须不变
        assert select_formation("感情过去未来") == "恋羽十字"

    def test_specific_words_follow_keyword_map(self):
        """两张表一处定义：选阵词表新增的词，每日牌运判定自动跟上。"""
        from data.plugins.astrbot_plugin_star_feather.intent import (
            KEYWORD_MAP,
            SPECIFIC_WORDS,
        )
        missing = [w for group, _ in KEYWORD_MAP[:2] for w in group
                   if w not in SPECIFIC_WORDS]
        assert not missing, f"选阵词表有词没进 SPECIFIC_WORDS：{missing}"


class TestBuildRequest:
    """ReadingRequest 的构造口径：三个入口共用这一个判定入口。"""

    def test_help_short_circuits(self):
        from data.plugins.astrbot_plugin_star_feather.intent import (
            KIND_HELP,
            build_request,
        )
        assert build_request("auto", "帮助").kind == KIND_HELP
        assert build_request("single", "怎么用").kind == KIND_HELP

    def test_daily_kind_respects_switch(self):
        from data.plugins.astrbot_plugin_star_feather.intent import build_request
        req = build_request("auto", "今日运势")
        assert req.kind == "daily" and req.fixed_for_day and req.spread == "羽签"
        # 开关关闭：普通问法不再路由到固定牌运
        assert build_request("auto", "今日运势", daily_enabled=False).kind == "spread"
        # 显式 /今日牌运 仍走 daily，但 fixed 跟随开关
        assert build_request("daily", "今日运势", daily_enabled=False).fixed_for_day is False

    def test_single_kind_is_never_fixed(self):
        from data.plugins.astrbot_plugin_star_feather.intent import build_request
        req = build_request("single", " 帮我算一卦 ")
        assert req.kind == "single" and req.spread == "羽签" and req.fixed_for_day is False
        assert req.question == ""

    def test_tool_hint_only_when_rules_have_no_signal(self):
        from data.plugins.astrbot_plugin_star_feather.intent import build_request
        # 规则没信号：采纳模型给的枚举值
        assert build_request("auto", "今天天气不错", source="tool",
                             formation_hint="羽镜").spread == "羽镜"
        # 规则命中事业组：模型的话不算数
        assert build_request("auto", "考研复习", source="tool",
                             formation_hint="羽签").spread == "羽镜"
        # 枚举外的值一律忽略，回落默认阵
        assert build_request("auto", "今天天气不错", source="tool",
                             formation_hint="乱写的").spread == "羽时三刻"

    def test_command_never_accepts_a_hint(self):
        from data.plugins.astrbot_plugin_star_feather.intent import build_request
        assert build_request("auto", "今天天气不错", source="command",
                             formation_hint="羽镜").spread == "羽时三刻"

    def test_topic_bucket_matches_daily_cache(self):
        from data.plugins.astrbot_plugin_star_feather.intent import build_request
        assert build_request("daily", "今天运势").topic_key == "（今日牌运）"
        assert build_request("daily", "今天财运怎么样").topic_key == "今天财运怎么样"


class TestToolFormationHintAliases:
    def test_classic_alias_is_accepted_from_tool(self):
        req = build_request("auto", "今天天气不错", source="tool",
                            formation_hint="圣三角")
        assert req.spread == "羽镜"

    def test_unknown_hint_is_ignored(self):
        req = build_request("auto", "今天天气不错", source="tool",
                            formation_hint="不存在的阵")
        assert req.spread == "羽时三刻"
