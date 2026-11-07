// 牌面图片：缩略图缓存、请求合并、懒加载与牌背解码。
import { el } from "./dom.js";
import { isDemo, apiGet, demoThumbURL, CARD_BACK_ID } from "./api.js";

const thumbCache = new Map();
const thumbInflight = new Map();
let cardBackURL = "";
let cardBackPromise = null;

const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    observer.unobserve(entry.target);
    loadThumb(entry.target);
  }
}, { rootMargin: "160px" });

export async function thumbURL(cardId, upright, width) {
  const key = `${cardId}|${upright ? 1 : 0}|${width}`;
  if (thumbCache.has(key)) return thumbCache.get(key);
  if (isDemo) {
    const url = demoThumbURL(cardId, upright, width);
    thumbCache.set(key, url);
    return url;
  }
  // 同一张牌、同一尺寸会被多个 <img> 同时要（列表里同牌用户、图鉴与详情）；
  // 复用进行中的请求，别让 base64 传两遍。
  const pending = thumbInflight.get(key);
  if (pending) return pending;
  const request = (async () => {
    const res = await apiGet("card-image", { id: cardId, upright: upright ? 1 : 0, w: width });
    const url = `data:${res.mime};base64,${res.base64}`;
    thumbCache.set(key, url);
    return url;
  })();
  thumbInflight.set(key, request);
  try {
    return await request;
  } finally {
    // 失败也要清理，否则这张牌会一直拿到同一个 rejected Promise
    thumbInflight.delete(key);
  }
}

export async function ensureCardBack(width = 150) {
  if (cardBackURL) return cardBackURL;
  // 并发调用共享同一次解码：预览表单与抽牌渲染会同时要牌背
  if (!cardBackPromise) {
    cardBackPromise = (async () => {
      const url = await thumbURL(CARD_BACK_ID, true, width);
      if (!url) return "";
      await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = resolve;
        img.onerror = reject;
        img.src = url;
      });
      cardBackURL = url;
      return url;
    })().finally(() => {
      // 失败也要清掉，下次调用能重新解码
      cardBackPromise = null;
    });
  }
  return cardBackPromise;
}

export function getCardBackURL() {
  return cardBackURL;
}

export function loadThumb(img) {
  if (img._thumbPromise) return img._thumbPromise;
  img._thumbPromise = (async () => {
    if (img.dataset.loaded) return;
    img.dataset.loaded = "1";
    const holder = img.closest(".pick-thumb");
    const card = img.closest(".pick-card");
    try {
      const url = await thumbURL(img.dataset.cardId, img.dataset.upright === "1",
                                Number(img.dataset.width));
      if (!url) throw new Error("empty thumbnail");
      img.src = url;
      // 解码完成后再揭开：预览卡的占位层只在真正能画出图时退场
      await img.decode();
      holder?.classList.add("thumb-ready");
      card?.classList.add("card-ready");
    } catch (error) {
      console.warn("card-image failed", img.dataset.cardId, error);
      img.classList.add("thumb-error");
      img.alt = "图片加载失败";
      holder?.classList.add("thumb-ready");
      card?.classList.add("card-ready");
    }
  })();
  return img._thumbPromise;
}

export function makeThumb(cardId, upright, width, alt) {
  const img = el("img", "thumb");
  img.alt = alt || "";
  img.loading = "lazy";
  img.dataset.cardId = cardId;
  img.dataset.upright = upright ? "1" : "0";
  img.dataset.width = String(width);
  observer.observe(img);
  return img;
}
