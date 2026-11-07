// 只放被多个背景模块共享的几何计算：目前只有 backdropMetrics。
// 纯函数、不读 DOM。色相场与地平线淡出只服务近景流星，留在 near-meteor.js。
export function backdropMetrics(vw, vh) {
  const scale = Math.max(vw / 5120, vh / 2880);
  const dispW = 5120 * scale;
  const dispH = 2880 * scale;
  return {
    scale,
    dispW,
    dispH,
    offsetX: (vw - dispW) * 0.62,
    offsetY: (vh - dispH) / 2,
  };
}
