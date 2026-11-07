import { $, el } from "../dom.js";

// 风雪：雪片整体被风往右下吹，途中沿风向摆动
export function initSnow() {
  const box = $("snow");
  if (!box || box.childElementCount) return;
  const count = 72;
  for (let i = 0; i < count; i += 1) {
    const flake = el("span", "snowflake");
    // 近处的雪片更大更亮、落得更快，远处更小更淡，拉开纵深
    const depth = Math.random();
    const size = 1.4 + depth * depth * 4.4;
    flake.style.left = `${Math.random() * 100}%`;
    flake.style.width = `${size}px`;
    flake.style.height = `${size}px`;
    flake.style.setProperty("--drift", `${14 + Math.random() * 26 + depth * 12}vw`);
    flake.style.setProperty("--sway", `${1 + Math.random() * 3}vw`);
    flake.style.setProperty(
      "--flake-opacity", (0.2 + depth * 0.65).toFixed(2),
    );
    flake.style.animationDuration = `${16.5 - depth * 8 + Math.random() * 2}s`;
    flake.style.animationDelay = `${-Math.random() * 18}s`;
    box.append(flake);
  }
}

export function destroySnow() {
  $("snow")?.replaceChildren();
}
