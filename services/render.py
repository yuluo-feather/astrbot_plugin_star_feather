"""渲染服务：实例图片目录、渲染并发闸、海报卡与发送前核验。

渲染实现仍在 card_render.py（第 5 步再拆布局/素材/存储）；本层只做编排：
text_only 不渲染、渲染丢线程池、并发上限、失败一律降级为 None。

照片清理仍注册在图片产生点（渲染成功后），不在发送出口。
"""
import asyncio
import os

from astrbot.api import logger

from ..card_render import (
    _render_daily_card_img,
    _schedule_image_cleanup,
    render_cards,
)
from ..core.models import Pick
from ..dailylines import pick_signature


class Renderer:
    """牌面图与今日牌运海报卡的渲染出口。"""

    def __init__(self, image_dir: str, send_mode: str):
        self.image_dir = image_dir
        self.send_mode = send_mode
        # 渲染并发锁：Pillow 全尺寸合成内存峰值不小，防多人同时占卜时暴涨。
        self._lock = asyncio.Semaphore(2)

    def _render_sync(self, formation: str, positions: list[str], picks: list[Pick]) -> str | None:
        try:
            path = render_cards(positions, picks, formation, save_dir=self.image_dir)
        except Exception as e:
            logger.warning(f"牌面图片渲染失败，回退文字版: {e}")
            return None
        if not isinstance(path, str) or not path.strip():
            logger.warning("牌面图片渲染返回空值，回退文字版")
            return None
        return path

    async def card(self, formation: str, positions: list[str],
                   picks: list[Pick]) -> str | None:
        """普通占卜牌面图；text_only 不渲染（省资源），返回 None。"""
        if self.send_mode == "text_only":
            return None
        async with self._lock:
            img = await asyncio.to_thread(self._render_sync, formation, positions, picks)
        _schedule_image_cleanup(img)
        return img

    async def daily_card(self, picks: list[Pick], uid: str, clock) -> str | None:
        """今日牌运海报卡：牌面 + 日期 + 池内当日签文 + 署名。

        失败返回 None，调用方回退普通牌面图；清理走同一条保险丝。
        """
        if not picks:
            return None
        try:
            card = picks[0]["card"]
            upright = bool(picks[0]["upright"])
            date_text = clock.date_text()
            signature = pick_signature(card, upright, uid, clock.today())
            async with self._lock:
                img = await asyncio.to_thread(_render_daily_card_img, card, upright,
                                              signature, date_text, self.image_dir)
            if not isinstance(img, str) or not img.strip():
                return None
            _schedule_image_cleanup(img)
            return img
        except Exception as e:
            logger.warning(f"今日牌运卡渲染失败，回退普通牌面图: {e}")
            return None

    async def ensure_image(self, img: str | None, formation: str, positions: list[str],
                           picks: list[Pick]) -> str | None:
        """发送前核验：图不在或为空就补渲染一次，再失败就降级为无图。

        守的是出口纪律「图片问题绝不带走消息」，不是图片年龄——渲染已排到
        两段 AI 之后，距平台读文件只剩毫秒。
        """
        if not isinstance(img, str) or not img:
            return img
        try:
            size = os.path.getsize(img)
        except (OSError, ValueError) as e:
            size, why = 0, f"核验失败：{e}"
        else:
            why = "是空文件"
        if size > 0:
            return img
        logger.warning(f"牌面图在发送前不可用（{why}）：{img}")
        try:
            new_img = await self.card(formation, positions, picks)
        except Exception as e:
            logger.warning(f"重渲染牌面图异常，本签降级为无图: {e}")
            return None
        if not new_img:
            logger.warning("重渲染仍失败，本签降级为无图（解读附带逐牌牌义）")
        return new_img
