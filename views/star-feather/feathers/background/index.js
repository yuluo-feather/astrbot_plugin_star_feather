import { $ } from "../dom.js";
import { layoutBackdrop } from "./layout.js";
import { NearMeteors } from "./near-meteor.js";
import { FarMeteors } from "./far-meteor.js";

// 背景生命周期的统一入口：全局监听只在这里注册，各效果模块只提供方法。
// resize 顺序固定：布局 → 近景（它自己会重算色相场与画布尺寸）。
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let near = null;
let far = null;
let inited = false;
let started = false;

function ensureNear() {
  if (near) return near;
  const sky = $("meteor-canvas");
  const water = $("meteor-water");
  if (!sky || !water) return null;
  near = new NearMeteors(sky, water);
  return near;
}

export function init() {
  layoutBackdrop();
  if (inited) return;
  inited = true;
  window.addEventListener("resize", resize);
  reducedMotion.addEventListener("change", onReducedMotionChange);
}

export function start() {
  if (started || reducedMotion.matches) return;
  started = true;
  if (!far) far = new FarMeteors();
  far.start();
  ensureNear()?.start();
}

export function stop() {
  started = false;
  far?.stop();
  near?.stop();
}

export function destroy() {
  stop();
  window.removeEventListener("resize", resize);
  reducedMotion.removeEventListener("change", onReducedMotionChange);
  inited = false;
  near = null;
  far = null;
}

export function resize() {
  layoutBackdrop();
  near?.resize();
}

function onReducedMotionChange(event) {
  if (event.matches) stop();
  else start();
}
