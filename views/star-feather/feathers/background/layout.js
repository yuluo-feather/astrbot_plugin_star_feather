import { backdropMetrics } from "./geometry.js";

// 地平线在视口里的位置：与背景 cover 的缩放/居中保持一致，倒影层据此翻转
export function layoutBackdrop() {
  const horizon = 2014;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const { scale, offsetY } = backdropMetrics(vw, vh);
  const hy = offsetY + horizon * scale;
  document.documentElement.style.setProperty("--horizon-y", `${hy.toFixed(1)}px`);
}
