"""结果呈现：按确定顺序发送牌面/牌灵的话/解读段/收尾句。

发送顺序由 `await` 保证：结果对象落地后才发收尾句，不再靠固定等待窗口。
工具入口仍由调用方在最后 yield 一个字符串给 Agent Loop。
"""
from ..core.draw import render_text
from ..core.models import Reading
from ..deliver import Deliverer


class Presenter:
    def __init__(self, deliverer: Deliverer):
        self.deliverer = deliverer

    async def present(self, reading: Reading, messenger, event) -> None:
        """把一次占卜的结果发出去；收尾句排在结果之后。"""
        async for result in self.deliverer.deliver(
                event, reading.interpretation, reading.image, reading.formation,
                list(reading.positions), list(reading.picks), render_text,
                fail_note=reading.fail_note, preface=reading.spirit):
            await messenger.send_result(result)
        if reading.epilogue:
            await messenger.send_text(reading.epilogue)
