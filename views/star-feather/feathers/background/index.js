import { $ } from "../dom.js";
import { layoutBackdrop } from "./layout.js";
import { NearMeteors } from "./near-meteor.js";
import { FarMeteors } from "./far-meteor.js";
import { initStars, destroyStars } from "./stars.js";
import { initDust, destroyDust } from "./dust.js";
import { initWind, destroyWind } from "./wind.js";
import { initSnow, destroySnow } from "./snow.js";
import { Environment } from "./environment.js";

// 背景生命周期的统一入口：全局监听只在这里注册，各效果模块只提供方法。
// resize 顺序固定：布局 → 近景（它自己会重算色相场与画布尺寸）→ 环境系统。
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let near = null;
let far = null;
let environment = null;
let inited = false;
let started = false;
let visible = !document.hidden;
let motionAllowed = !reducedMotion.matches;
let mode = "canvas";

function readMode() {
  const value = new URLSearchParams(window.location.search).get("bg");
  if (value === "legacy") return "legacy";
  if (value === "nowind" || value === "nogust") return value;
  return "canvas";
}

function ensureNear() {
  if (near) return near;
  const sky = $("meteor-canvas");
  const water = $("meteor-water");
  if (!sky || !water) return null;
  near = new NearMeteors(sky, water);
  return near;
}

function ensureEnvironment() {
  if (environment) return environment;
  const canvas = $("environment-canvas");
  if (!canvas) return null;
  environment = new Environment(canvas, {
    wind: mode !== "nowind",
    gust: mode !== "nowind" && mode !== "nogust",
  });
  return environment;
}

// 单一状态机：reduced-motion 优先，其次页面隐藏
function reconcileEnvironment() {
  if (!environment || mode === "legacy") return;
  if (!motionAllowed) environment.stop();
  else if (!visible) environment.pause();
  else environment.start();
}

export function init() {
  layoutBackdrop();
  if (inited) return;
  inited = true;
  mode = readMode();
  initStars();
  initDust();
  initWind();
  if (mode === "legacy") {
    initSnow();
    // legacy 模式不启动环境系统，画布保持隐藏，避免两套效果叠加
    const canvas = $("environment-canvas");
    if (canvas) canvas.style.display = "none";
  } else {
    ensureEnvironment();
  }
  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", onVisibilityChange);
  reducedMotion.addEventListener("change", onReducedMotionChange);
}

export function start() {
  if (started || !motionAllowed) return;
  started = true;
  if (!far) far = new FarMeteors();
  far.start();
  ensureNear()?.start();
  if (mode !== "legacy") ensureEnvironment()?.start();
}

export function stop() {
  started = false;
  far?.stop();
  near?.stop();
  if (mode !== "legacy") environment?.stop();
}

export function destroy() {
  stop();
  destroyStars();
  destroyDust();
  destroyWind();
  destroySnow();
  environment?.destroy();
  window.removeEventListener("resize", resize);
  document.removeEventListener("visibilitychange", onVisibilityChange);
  reducedMotion.removeEventListener("change", onReducedMotionChange);
  inited = false;
  near = null;
  far = null;
  environment = null;
}

export function resize() {
  layoutBackdrop();
  near?.resize();
  environment?.resize();
}

function onReducedMotionChange(event) {
  motionAllowed = !event.matches;
  if (event.matches) {
    stop();
  } else {
    started = false;
    start();
  }
}

function onVisibilityChange() {
  visible = !document.hidden;
  reconcileEnvironment();
}
