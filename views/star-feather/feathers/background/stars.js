import { $, el } from "../dom.js";

// 星空：DOM 星点 + 水面倒影，闪烁交给 CSS 动画
export function initStars() {
  const box = $("sky-stars");
  if (!box || box.childElementCount) return;
  const count = 80;
  for (let i = 0; i < count; i += 1) {
    const star = el("span", "star");
    const size = 9 + Math.random() * 17;
    // 星云在右侧更密：多数星星落在右半，左上留出深空
    const x = Math.random() < 0.55 ? 42 + Math.random() * 58 : Math.random() * 58;
    star.style.left = `${x}%`;
    star.style.top = `${Math.pow(Math.random(), 0.85) * 68}%`;
    star.style.width = `${size}px`;
    star.style.height = `${size}px`;
    star.style.opacity = `${0.2 + Math.random() * 0.6}`;
    star.style.animationDuration = `${3 + Math.random() * 5}s`;
    star.style.animationDelay = `${Math.random() * 4}s`;
    if (Math.random() < 0.08) star.classList.add("bright");
    box.append(star);
  }
  // 倒影层复用同一批星点：位置、亮度和闪烁相位一致，靠水面波纹产生错动
  const mirror = $("water-stars");
  if (mirror && !mirror.childElementCount) {
    for (const star of box.children) mirror.append(star.cloneNode(true));
  }
}

export function destroyStars() {
  $("sky-stars")?.replaceChildren();
  $("water-stars")?.replaceChildren();
}
