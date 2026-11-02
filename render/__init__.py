"""渲染包：布局（layout）、素材清单（assets）、绘制（draw）、文件生命周期（store）。

对外保持一个入口：`render.render_cards` / `render._render_daily_card_img` /
`render._schedule_image_cleanup` / `render.new_instance_image_dir` 等名字从这里导出。
"""
import tempfile  # noqa: F401  （测试与诊断按旧口径替换 render.tempfile）

from ..fonts import _load_font  # noqa: F401
from .assets import (  # noqa: F401
    _ASSET_ROOT,
    _asset_path,
    _asset_path_by_id,
    asset_manifest,
)
from .draw import (  # noqa: F401
    _OPAQUE_BACKDROP,
    _RESAMPLE,
    _RNG,
    OUTPUT_QUALITY,
    _background_cached,
    _build_card_background,
    _draw_capsule,
    _draw_card,
    _load_card_image,
    _render_daily_card_img,
    _save_image,
    _split_signature_lines,
    _text_center,
    _wrap_text,
    render_cards,
)
from .layout import (  # noqa: F401
    BAR_TEXT,
    BAR_TITLE,
    CAPSULE_BG,
    CAPSULE_TEXT,
    CARD_BORDER,
    CARD_H,
    CARD_OUT_W,
    DAILY_CARD_H,
    DAILY_CARD_QUOTE_Y,
    DAILY_CARD_W,
    DAILY_CARD_Y,
    DAILY_DATE_TEXT,
    DAILY_FOOT_TEXT,
    DAILY_QUOTE_GAP,
    DAILY_SIG_TEXT,
    DAILY_SIGN_TEXT,
    GAP,
    INFO_H,
    INNER_H,
    INNER_W,
    LABEL_H,
    LINE,
    PAD,
    PAD_X,
    RED,
    TITLE_H,
    CardGrid,
    card_grid,
)
from .store import (  # noqa: F401
    _CLEANUP_TASKS,
    _INSTANCE_DIR_RE,
    IMAGE_DIR_ROOT,
    IMAGE_TTL_SECONDS,
    STALE_INSTANCE_DIR_AGE,
    _delayed_remove,
    _is_stale,
    _private_fallback_dir,
    _schedule_image_cleanup,
    cleanup_stale_images,
    new_instance_image_dir,
)
