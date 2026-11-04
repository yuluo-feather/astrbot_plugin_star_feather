"""结果呈现：按确定顺序发送牌面/牌灵的话/解读段/收尾句。

发送顺序由 `await` 保证：结果对象落地后才发收尾句，不再靠固定等待窗口。
工具入口仍由调用方在最后 yield 一个字符串给 Agent Loop。
"""
from ..core.draw import render_text
from ..core.dto import DeliveryCapabilities
from ..core.models import Reading
from ..core.ports import ResultMessenger
from .deliver import Deliverer


class Presenter:
    def __init__(self, deliverer: Deliverer):
        self.deliverer = deliverer

    def _fallback_text(self, reading: Reading) -> str:
        """结果发送失败时的纯文字兜底：不带图、不带合并转发。"""
        parts = []
        if reading.spirit:
            parts.append(reading.spirit)
        if reading.interpretation:
            parts.append(reading.interpretation.text)
        else:
            parts.append(render_text(reading.formation, list(reading.positions),
                                     list(reading.picks)))
        if reading.fail_note:
            parts.append(reading.fail_note)
        if self.deliverer.disclaimer:
            parts.append(self.deliverer.disclaimer)
        return "\n\n".join(part for part in parts if part)

    async def present(self, reading: Reading, messenger: ResultMessenger,
                      caps: DeliveryCapabilities) -> bool:
        """把一次占卜的结果发出去；收尾句排在结果之后。

        合并转发 / 图文链发送失败时降级为纯文字重发：用户至少能拿到解读，
        不会只剩一句收尾。收尾句只在结果确认送达后发送。

        Args:
            reading: 本次占卜结果。
            messenger: 消息端口，发送失败时返回 False。
            caps: 本次投递能力（是否合并转发、转发 uin）。

        Returns:
            至少一次结果发送未报错（主结果或纯文字补发之一）。收尾句不计入：
            它不能反过来证明结果已经送达。
        """
        delivered = False
        async for result in self.deliverer.deliver(
                messenger, caps, reading.interpretation, reading.image, reading.formation,
                list(reading.positions), list(reading.picks), render_text,
                fail_note=reading.fail_note, preface=reading.spirit):
            if await messenger.send_result(result):
                delivered = True
                continue
            fallback = self._fallback_text(reading)
            if fallback and await messenger.send_text(fallback):
                delivered = True
        if reading.epilogue and delivered:
            await messenger.send_text(reading.epilogue)
        return delivered
