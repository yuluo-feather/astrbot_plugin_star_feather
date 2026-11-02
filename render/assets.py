"""素材清单：牌 id → 文件（assets/manifest.json）。

不再按 .png/.webp 猜后缀：清单由 assets/manifest.json 一次写定，
缺条目就是缺素材，直接报出来。
"""
import json
import os

_ASSET_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
_MANIFEST_PATH = os.path.join(_ASSET_ROOT, "manifest.json")


def _load_manifest() -> dict:
    try:
        with open(_MANIFEST_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


_MANIFEST = _load_manifest()


def asset_manifest() -> dict:
    return dict(_MANIFEST)


def _asset_path_by_id(card_id: str | None) -> str:
    """牌 id -> 素材文件绝对路径；清单里没有就报出来。"""
    rel = _MANIFEST.get(card_id) if isinstance(card_id, str) else None
    if not rel:
        raise FileNotFoundError(f"素材清单缺少牌 {card_id!r}（assets/manifest.json）")
    return os.path.join(_ASSET_ROOT, rel)


def _asset_path(card) -> str:
    """tarot_data 牌字典 -> 素材文件绝对路径。"""
    card_id = card.get("id") if isinstance(card, dict) else None
    return _asset_path_by_id(card_id)
