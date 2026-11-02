"""结果呈现：按确定顺序发送牌面/牌灵的话/解读段/收尾句。

发送顺序由 `await` 保证：结果对象落地后才发收尾句，不再靠固定等待窗口。
工具入口仍由调用方在最后 yield 一个字符串给 Agent Loop。
"""
from ..core.draw import render_text
from ..core.models import Reading
from .deliver import Deliverer


class Presenter:
    def __init__(self, deliverer: Deliverer):
        self.deliverer = deliverer

    @staticmethod
    def _fallback_text(reading: Reading) -> str:
        """结果发送失败时的纯文字兜底：不带图、不带合并转发。"""
        parts = []
        if reading.spirit:
            parts.append(reading.spirit)
        if reading.interpretation:
            parts.append(str(reading.interpretation))
        else:
            parts.append(render_text(reading.formation, list(reading.positions),
                                     list(reading.picks)))
        if reading.fail_note:
            parts.append(reading.fail_note)
        return "\n\n".join(part for part in parts if part)

    async def present(self, reading: Reading, messenger, event) -> None:
        """把一次占卜的结果发出去；收尾句排在结果之后。

        合并转发 / 图文链发送失败时降级为纯文字重发：用户至少能拿到解读，
        不会只剩一句收尾。
        """
        async for result in self.deliverer.deliver(
                event, reading.interpretation, reading.image, reading.formation,
                list(reading.positions), list(reading.picks), render_text,
                fail_note=reading.fail_note, preface=reading.spirit):
            if not await messenger.send_result(result):
                fallback = self._fallback_text(reading)
                if fallback:
                    await messenger.send_text(fallback)
        if reading.epilogue:
            await messenger.send_text(reading.epilogue)
