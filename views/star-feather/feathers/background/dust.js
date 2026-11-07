import { $, el } from "../dom.js";

// 星尘：不落，只在原地附近缓慢漂荡，明暗各自呼吸
export function initDust() {
  const box = $("dust");
  if (!box || box.childElementCount) return;
  const count = 56;
  for (let i = 0; i < count; i += 1) {
    const mote = el("span", "dust-mote");
    const size = 1.6 + Math.random() * 2.6;
    const roll = Math.random();
    if (roll < 0.12) mote.classList.add("gold");
    else if (roll < 0.24) mote.classList.add("cyan");
    mote.style.left = `${Math.random() * 100}%`;
    mote.style.top = `${Math.random() * 96}%`;
    mote.style.width = `${size}px`;
    mote.style.height = `${size}px`;
    mote.style.setProperty("--dx", `${(Math.random() * 2 - 1) * 4}vw`);
    mote.style.setProperty("--dy", `${(Math.random() * 2 - 1) * 3}vh`);
    mote.style.animationDuration = `${16 + Math.random() * 24}s`;
    mote.style.animationDelay = `${-Math.random() * 30}s`;
    mote.style.setProperty("--twinkle-duration", `${2.6 + Math.random() * 3.4}s`);
    mote.style.setProperty("--twinkle-delay", `${-Math.random() * 6}s`);
    box.append(mote);
  }
}

export function destroyDust() {
  $("dust")?.replaceChildren();
}
