"""记忆主题分类器：只输出固定枚举或空串，绝不保存原始问题。

`core/intent.py::norm_topic()` 带具体主题词时返回的是清洗后的原问题，只适合
解读缓存分桶；事件里的 `topic_key` 必须走这里。
"""
from __future__ import annotations

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "工作": ("工作", "事业", "职场", "创业", "转行", "求职", "面试", "升职"),
    "学业": ("学业", "考试", "学习", "升学", "论文", "毕业"),
    "感情": ("感情", "恋爱", "喜欢", "桃花", "关系", "婚姻", "结婚", "姻缘", "相亲", "离婚"),
    "家庭": ("家庭", "家人", "父母", "孩子", "亲子", "婆媳"),
    "财务": ("财运", "钱财", "收入", "理财", "投资", "金钱", "债务"),
    "健康": ("健康", "身体", "养生", "生病", "失眠", "疾病"),
    "人际": ("人际", "人缘", "朋友", "社交", "圈子"),
    "出行": ("出行", "旅行", "搬家", "房子", "买房", "租房"),
}

TOPIC_KEYS: tuple[str, ...] = tuple(TOPIC_KEYWORDS)


def classify_topic(question: str) -> str:
    """固定关键词分类；命中多个主题或没有命中都返回空串。"""
    text = question or ""
    if not text:
        return ""
    hits = [topic for topic, words in TOPIC_KEYWORDS.items()
            if any(word in text for word in words)]
    return hits[0] if len(hits) == 1 else ""
