import { $, el } from "../dom.js";

// 风：几道横风带从左侧扫过，配合被吹斜的雪
export function initWind() {
  const box = $("wind");
  if (!box || box.childElementCount) return;
  const count = 10;
  for (let i = 0; i < count; i += 1) {
    const gust = el("span", "gust");
    const duration = 6 + Math.random() * 6;
    gust.style.top = `${4 + Math.random() * 62}%`;
    gust.style.width = `${24 + Math.random() * 34}vw`;
    gust.style.setProperty("--gust-opacity", (0.08 + Math.random() * 0.14).toFixed(2));
    gust.style.animationDuration = `${duration}s`;
    gust.style.animationDelay = `${-Math.random() * duration}s`;
    box.append(gust);
  }
}

export function destroyWind() {
  $("wind")?.replaceChildren();
}
