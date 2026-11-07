import { $, el } from "../dom.js";

// 远景 CSS 流星：短促的 DOM 节点 + 金色余晖，节奏与近景 Canvas 完全独立。
// 生成、连发与清理都留在模块内部，对外只给 start / stop。
export class FarMeteors {
  constructor() {
    this.enabled = false;
    this.timer = 0;
    this.burstTimers = new Set();
    this.emberTimers = new Set();
  }

  start() {
    if (this.enabled) return;
    this.enabled = true;
    this.spawn();
    this.schedule();
  }

  stop() {
    this.enabled = false;
    clearTimeout(this.timer);
    this.timer = 0;
    for (const timer of this.burstTimers) clearTimeout(timer);
    this.burstTimers.clear();
    for (const timer of this.emberTimers) clearTimeout(timer);
    this.emberTimers.clear();
    this.clearNodes();
  }

  clearNodes() {
    // 减动效下 CSS 把 animation 设为 none，浏览器触发的是 animationcancel 而不是
    // animationend：节点不会自己走掉，stop 时必须主动收
    const sky = document.querySelector(".sky");
    const pool = $("water-meteors");
    if (sky) {
      for (const node of sky.querySelectorAll(".meteor:not(.reflect), .ember")) node.remove();
    }
    if (pool) {
      for (const node of pool.querySelectorAll(".meteor.reflect")) node.remove();
    }
  }

  schedule() {
    // 远景保持 0.8–1.9 秒的频率（30% 连发）
    const delay = 800 + Math.random() * 1100;
    this.timer = setTimeout(() => {
      if (!this.enabled) return;
      const bursts = Math.random() < 0.3 ? 2 : 1;
      for (let i = 0; i < bursts; i += 1) {
        const id = setTimeout(() => {
          // 先删登记：删不到说明已被 stop 清掉，别在停止后补生成
          if (!this.burstTimers.delete(id)) return;
          if (!this.enabled) return;
          this.spawn();
        }, i * (140 + Math.random() * 220));
        this.burstTimers.add(id);
      }
      this.schedule();
    }, delay);
  }

  spawn() {
    if (!this.enabled) return;
    const sky = document.querySelector(".sky");
    if (!sky) return;
    const xPct = 24 + Math.random() * 58;
    const yPct = Math.random() * 38;
    const duration = 0.9 + Math.random() * 0.7;
    const angle = 24 + Math.random() * 16;
    // A 背景流星：短、细、偏暗；B 纵深流星：更长、更亮、长度差异大
    const kind = Math.random() < 2 / 3 ? "a" : "b";
    const len = kind === "a" ? 90 + Math.random() * 50 : 140 + Math.random() * 80;
    const travel = kind === "a" ? 260 + Math.random() * 60 : 300 + Math.random() * 80;
    const peak = kind === "a" ? 0.45 + Math.random() * 0.2 : 0.6 + Math.random() * 0.25;

    const meteor = el("span", "meteor");
    meteor.style.left = `${xPct}%`;
    meteor.style.top = `${yPct}%`;
    meteor.style.animationDuration = `${duration}s`;
    meteor.style.setProperty("--angle", `${angle.toFixed(1)}deg`);
    meteor.style.setProperty("--len", `${len.toFixed(0)}px`);
    meteor.style.setProperty("--travel", `${travel.toFixed(0)}px`);
    meteor.style.setProperty("--peak", peak.toFixed(2));
    meteor.dataset.kind = kind;
    meteor.addEventListener("animationend", () => meteor.remove());
    sky.append(meteor);

    // 金色余晖：沿轨迹撒几颗火星，各自飘散淡出
    const rad = (angle * Math.PI) / 180;
    const dirX = Math.cos(rad);
    const dirY = Math.sin(rad);
    const startX = (xPct / 100) * window.innerWidth;
    const startY = (yPct / 100) * window.innerHeight;
    for (let i = 0; i < 5; i += 1) {
      const at = 0.12 + i * 0.14;
      const id = setTimeout(() => {
        // 与连发定时器同一套代际校验：stop 清空集合后，已排队的旧回调自行失效
        if (!this.emberTimers.delete(id)) return;
        const along = 140 + travel * at;
        const ember = el("span", "ember");
        const size = 2 + Math.random() * 2.5;
        ember.style.width = `${size}px`;
        ember.style.height = `${size}px`;
        ember.style.left = `${startX + dirX * along + (Math.random() - 0.5) * 16}px`;
        ember.style.top = `${startY + dirY * along + (Math.random() - 0.5) * 16}px`;
        ember.style.setProperty("--ex", `${(Math.random() - 0.5) * 18}px`);
        ember.style.setProperty("--ey", `${-14 - Math.random() * 20}px`);
        ember.style.animationDuration = `${0.8 + Math.random() * 0.6}s`;
        ember.addEventListener("animationend", () => ember.remove());
        // 后台标签页里动画不推进时兜底清理
        setTimeout(() => ember.remove(), 1800);
        sky.append(ember);
      }, at * duration * 1000);
      this.emberTimers.add(id);
    }

    const pool = $("water-meteors");
    if (!pool) return;
    const horizonY = parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--horizon-y"),
    ) || window.innerHeight * 0.7;
    const waterH = window.innerHeight - horizonY;
    if (waterH <= 0) return;
    // 倒影：以水线为界做垂直镜像并按水面高度压缩，越高的流星倒影越贴近水线
    const echo = el("span", "meteor reflect");
    echo.style.left = `${xPct}%`;
    echo.style.top = `${(waterH * (1 - yPct / 68)).toFixed(1)}px`;
    echo.style.animationDuration = `${duration}s`;
    echo.style.setProperty("--angle", `${angle.toFixed(1)}deg`);
    echo.style.setProperty("--len", `${len.toFixed(0)}px`);
    echo.style.setProperty("--travel", `${travel.toFixed(0)}px`);
    echo.style.setProperty("--peak", peak.toFixed(2));
    echo.dataset.kind = kind;
    echo.addEventListener("animationend", () => echo.remove());
    pool.append(echo);
  }
}
