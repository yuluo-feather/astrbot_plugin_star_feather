"""AstrBot Provider 网关：Context → 候选句柄 / 文本请求。

只做适配，不重定义错误语义：查询与请求异常原样上抛，由 AiInterpreter 现有的
try/except、来源名告警与失败冷却处理；不加重试、超时或并发闸。
"""


class AstrBotProviderGateway:
    """结构上满足 ai.gateway.ProviderGateway；这里不 import 只为避免类型层耦合。"""
    def __init__(self, context):
        self.context = context

    async def current(self, umo):
        return await self.context.get_using_provider_async(umo)

    def all(self):
        return list(self.context.get_all_providers())

    def identify(self, handle):
        """Provider 的稳定 id；异常交给解释器兜底（保留原有的逐条告警）。"""
        return handle.meta().id

    async def chat(self, handle, prompt, system_prompt):
        """发一次文本请求，只取 completion_text；非字符串按空串，读取异常向上抛。"""
        resp = await handle.text_chat(
            prompt=prompt, session_id=None, contexts=[], image_urls=[],
            system_prompt=system_prompt,
        )
        text = resp.completion_text
        return text if isinstance(text, str) else ""
