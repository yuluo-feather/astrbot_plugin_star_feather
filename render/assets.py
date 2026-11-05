"""素材清单：牌 id → 文件（assets/manifest.json）。

清单给出文件名；加载时按同名扩展名取第一个存在的：`.png` → `.webp` → `.jpg`。
三种都没有才报错。
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

# 同名扩展名的取值顺序
_EXT_PREFERENCE = (".png", ".webp", ".jpg")


def asset_manifest() -> dict:
    return dict(_MANIFEST)


def _asset_path_by_id(card_id: str | None) -> str:
    """牌 id -> 素材文件绝对路径；清单里没有就报出来。

    清单给出文件名；同名的 .png / .webp / .jpg 按此顺序取第一个存在的，
    三种都没有才抛 FileNotFoundError。
    """
    rel = _MANIFEST.get(card_id) if isinstance(card_id, str) else None
    if not rel:
        raise FileNotFoundError(f"素材清单缺少牌 {card_id!r}（assets/manifest.json）")
    stem = os.path.splitext(os.path.join(_ASSET_ROOT, rel))[0]
    for ext in _EXT_PREFERENCE:
        candidate = stem + ext
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(f"塔罗素材缺失: {stem}（.png / .webp / .jpg 均不存在）")


def asset_path_by_id(card_id: str) -> str:
    """公开入口：牌 id -> 素材绝对路径（可视化视图的缩略图也走这一处）。"""
    return _asset_path_by_id(card_id)


def _asset_path(card) -> str:
    """tarot_data 牌字典 -> 素材文件绝对路径。"""
    card_id = card.get("id") if isinstance(card, dict) else None
    return _asset_path_by_id(card_id)
