// 星羽塔罗 · 观测台：Dashboard iframe 里的只读视图。
// 数据访问在 feathers/api.js，DOM 工具在 feathers/dom.js；这里只放页面功能与装配。
import { $, el, showToast } from "./feathers/dom.js";
import {
  isDemo, apiGet, apiPost, connect, demoThumbURL, demoBackURL, CARD_BACK_ID,
} from "./feathers/api.js";

const state = {
  items: [], selected: null, cards: [], spreads: [], reversed: false,
  overview: null, cardDetail: null, detailUpright: true,
  preview: null, aiEnabled: true,
  listIndexedTotal: 0, listShown: 0, listTruncated: false,
};
const thumbCache = new Map();
const thumbInflight = new Map();
let detailSeq = 0;
let cardBackURL = "";
let cardBackPromise = null;

const SUIT_NAMES = {
  major: "大阿卡纳", wands: "权杖", cups: "圣杯", swords: "宝剑", pentacles: "星币",
};

// 近流星固定青碧色；远景 CSS 流星另有固定淡青蓝，两套都不再随机取色
const METEOR_TINT = [96, 245, 235];

// 星芒呼吸：透明度与尺寸共用同一周期，尺寸略滞后；每颗流星独立相位
const BREATH_PERIOD_MIN = 1.4;
const BREATH_PERIOD_MAX = 1.7;
const BREATH_SIZE_LAG = -0.35; // 尺寸落后透明度约 0.35 rad
const STAR_ALPHA_MIN = 0.14;
const STAR_ALPHA_MAX = 0.34;
const STAR_SIZE_MIN = 14;
const STAR_SIZE_MAX = 18;
const HALO_ALPHA_MIN = 0.18;
const HALO_ALPHA_MAX = 0.32;
const HALO_RADIUS_MIN = 10;
const HALO_RADIUS_MAX = 13;

// 预渲染的柔光贴图，按颜色缓存
const glowCache = new Map();
const GLOW_CACHE_LIMIT = 96;

function glowSprite(rgb) {
  const key = rgb.join(",");
  if (glowCache.has(key)) return glowCache.get(key);
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const g = canvas.getContext("2d");
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  // 白色只留最中心的一小圈，对应 2–3px 的中心亮核；外围回到青碧色
  grad.addColorStop(0.1, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.6)`);
  grad.addColorStop(1, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0)`);
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  if (glowCache.size >= GLOW_CACHE_LIMIT) {
    const oldest = glowCache.keys().next().value;
    glowCache.delete(oldest);
  }
  glowCache.set(key, canvas);
  return canvas;
}

// ---- 近景局部色相区域（Local Hue Field） ----
// 固定背景坐标系中的一片色相区域；只作用于近景 Canvas，不改背景滤镜。
const HUE_BASE_HSL = { h: 176, s: 88, l: 67 };
const HUE_PERIOD_MS = 12500;
const HUE_CORE_RATIO = 0.62;
const HUE_BUCKET_DEG = 3;
const HUE_RGB_LIMIT = 8;
const HUE_GLOW_STEP_DEG = 5;
const HORIZON_FADE_PX = 48;
// 一段 run 内的地平线透明度跨度超过这个值就断开：线性渐变只在两个端点之间插值，
// 跨过整条淡出带的 run 会被抹平，断开后每段都能贴合实际值
const HORIZON_RUN_STEP = 0.2;

function horizonFadeAt(horizon, y) {
  return Math.max(0, Math.min(1, (horizon - y) / HORIZON_FADE_PX));
}
const HUE_FIELD_POINTS = [
  { u: 0.06, v: 0.74, w: 0.20 },
  { u: 0.24, v: 0.67, w: 0.25 },
  { u: 0.42, v: 0.55, w: 0.30 },
  { u: 0.60, v: 0.42, w: 0.34 },
  { u: 0.78, v: 0.26, w: 0.38 },
  { u: 0.96, v: 0.12, w: 0.40 },
];

const hueReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let hueAnim = null;

function getHueAnim() {
  if (hueAnim) return hueAnim;
  const skyHue = document.querySelector(".sky-hue");
  const anims = skyHue?.getAnimations?.() || [];
  hueAnim = anims.find((a) => a.animationName === "nebula-cycle") || null;
  return hueAnim;
}

hueReducedMotion.addEventListener("change", () => { hueAnim = null; });

function huePhase() {
  if (hueReducedMotion.matches) return 0;
  let raw = getHueAnim()?.currentTime;
  if (raw == null) {
    hueAnim = null;
    raw = getHueAnim()?.currentTime;
  }
  if (raw == null) return 0;
  return (((raw % HUE_PERIOD_MS) + HUE_PERIOD_MS) % HUE_PERIOD_MS) / HUE_PERIOD_MS;
}

function backdropMetrics(vw, vh) {
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

function buildHueField(vw, vh) {
  const m = backdropMetrics(vw, vh);
  return HUE_FIELD_POINTS.map((p) => ({
    x: m.offsetX + p.u * m.dispW,
    y: m.offsetY + p.v * m.dispH,
    half: (p.w * m.dispH) / 2,
  }));
}

function hueWeightAt(x, y, field) {
  if (!field || field.length < 2) return 0;
  let tMin = Infinity;
  for (let i = 0; i < field.length - 1; i += 1) {
    const a = field[i];
    const b = field[i + 1];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len2 = dx * dx + dy * dy;
    let s = len2 ? ((x - a.x) * dx + (y - a.y) * dy) / len2 : 0;
    s = Math.max(0, Math.min(1, s));
    const qx = a.x + s * dx;
    const qy = a.y + s * dy;
    const half = a.half + (b.half - a.half) * s;
    const t = Math.hypot(x - qx, y - qy) / Math.max(half, 1);
    if (t < tMin) tMin = t;
  }
  if (!Number.isFinite(tMin) || tMin >= 1) return 0;
  if (tMin <= HUE_CORE_RATIO) return 1;
  const u = (tMin - HUE_CORE_RATIO) / (1 - HUE_CORE_RATIO);
  return 1 - u * u * (3 - 2 * u);
}

function hslToRgb(h, s, l) {
  const hh = (((h % 360) + 360) % 360) / 360;
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const channel = (t) => {
    let v = t;
    if (v < 0) v += 1;
    if (v > 1) v -= 1;
    if (v < 1 / 6) return p + (q - p) * 6 * v;
    if (v < 1 / 2) return q;
    if (v < 2 / 3) return p + (q - p) * (2 / 3 - v) * 6;
    return p;
  };
  return [
    Math.round(channel(hh + 1 / 3) * 255),
    Math.round(channel(hh) * 255),
    Math.round(channel(hh - 1 / 3) * 255),
  ];
}

function mixRgb(a, b, t) {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

function rgbToHue(rgb) {
  const r = rgb[0] / 255;
  const g = rgb[1] / 255;
  const b = rgb[2] / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (!d) return 0;
  let h;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h *= 60;
  return (h + 360) % 360;
}

function maxChannelDiff(a, b) {
  return Math.max(
    Math.abs(a[0] - b[0]),
    Math.abs(a[1] - b[1]),
    Math.abs(a[2] - b[2]),
  );
}

function bodyAlpha(p) {
  if (p <= 0.55) return 0.40 * (p / 0.55);
  return 0.40 + 0.45 * ((p - 0.55) / 0.45);
}

function glowTint(weight, phase) {
  if (weight <= 0) return METEOR_TINT;
  const offset = Math.round((phase * 360) / HUE_GLOW_STEP_DEG) * HUE_GLOW_STEP_DEG;
  const w = Math.round(weight * 4) / 4;
  if (offset % 360 === 0 && w >= 1) return METEOR_TINT;
  const target = hslToRgb(
    HUE_BASE_HSL.h + offset,
    HUE_BASE_HSL.s / 100,
    HUE_BASE_HSL.l / 100,
  );
  return mixRgb(METEOR_TINT, target, w);
}

function spawnMeteor() {
  if (!farEnabled) return;
  const sky = document.querySelector(".sky");
  if (!sky) return;
  const xPct = 24 + Math.random() * 58;
  const yPct = Math.random() * 38;
  const duration = 0.9 + Math.random() * 0.7;
  const angle = 28 + Math.random() * 8;
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
    setTimeout(() => {
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

// 近流星的星芒贴图（对应原场景 star_shine 子粒子）。原图是白色，画到 canvas 前
// 先染成固定青碧色，白色只留给中心亮核和尾迹亮芯。
const starSprite = new Image();
const starTinted = document.createElement("canvas");
let starReady = false;
starSprite.onload = () => {
  starTinted.width = starSprite.naturalWidth;
  starTinted.height = starSprite.naturalHeight;
  const g = starTinted.getContext("2d");
  g.drawImage(starSprite, 0, 0);
  g.globalCompositeOperation = "source-in";
  g.fillStyle = `rgb(${METEOR_TINT.join(",")})`;
  g.fillRect(0, 0, starTinted.width, starTinted.height);
  starReady = true;
};
starSprite.src = "./assets/star.webp";

// 近流星：canvas 渲染。按「重力 + 阻力」逐帧积分，尾迹沿真实路径撒柔光精灵
// （原场景 ropetrail 的做法：精灵重叠、加法混合累积出亮芯）。
class NearMeteors {
  constructor(skyCanvas, waterCanvas) {
    this.sky = skyCanvas;
    this.water = waterCanvas;
    this.ctx = skyCanvas.getContext("2d");
    this.wctx = waterCanvas ? waterCanvas.getContext("2d") : null;
    this.list = [];
    this.groupList = [];
    this.dpr = 1;
    this.vw = 0;
    this.vh = 0;
    this.horizon = 0;
    this.waterH = 0;
    this.hueField = null;
    this.running = false;
    this.enabled = true;
    this.raf = 0;
    this.last = 0;
    this.resize();
  }

  resize() {
    const prevW = this.vw;
    const prevH = this.vh;
    this.dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.vw = window.innerWidth;
    this.vh = window.innerHeight;
    const hz = parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--horizon-y"),
    );
    this.horizon = Number.isFinite(hz) ? hz : this.vh * 0.7;
    this.waterH = Math.max(0, this.vh - this.horizon);
    this.hueField = buildHueField(this.vw, this.vh);
    this.fit(this.sky, this.vw, this.vh);
    if (this.water) this.fit(this.water, this.vw, this.waterH);
    // 存活的流星按视口比例重映射：否则窗口缩放后它们和色相区域、
    // 水面倒影都会错位。构造期 prevW/prevH 为 0，跳过。
    if (prevW > 0 && prevH > 0) this.rescale(prevW, prevH);
    this.paint();
  }

  rescale(prevW, prevH) {
    const sx = this.vw / prevW;
    const sy = this.vh / prevH;
    for (const list of [this.list, this.groupList]) {
      for (const m of list) {
        m.x *= sx;
        m.y *= sy;
        m.vx *= sx;
        m.vy *= sy;
        for (const point of m.trail) {
          point.x *= sx;
          point.y *= sy;
        }
      }
    }
  }

  fit(canvas, w, h) {
    canvas.width = Math.round(w * this.dpr);
    canvas.height = Math.round(h * this.dpr);
    canvas.getContext("2d").setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  weightAt(x, y) {
    if (y >= this.horizon) return 0;
    return hueWeightAt(x, y, this.hueField);
  }

  spawn(opts = {}) {
    const isGroup = opts.group === true;
    const target = isGroup ? this.groupList : this.list;
    // 单颗与流星群各自独立计时、各自独立上限
    const limit = isGroup ? 3 : 2;
    if (!this.enabled) return;
    if (!this.vw || !this.vh) this.resize();
    if (!this.vw || !this.vh) return;
    if (target.length >= limit) return;
    // Canvas 水平向右为 0°，Y 轴向下，正角度即右下方向
    let angleDeg;
    let x;
    let y;
    if (isGroup) {
      // 同组共用基础角度，每颗再各自浮动 ±2°，看起来同向但不机械平行
      const jittered = (opts.baseAngle ?? 33) + (Math.random() * 4 - 2);
      angleDeg = Math.min(36, Math.max(29, jittered));
      // 顶部进入 65%，左侧进入 35%
      const fromTop = Math.random() < 0.65;
      x = fromTop
        ? this.vw * (0.08 + Math.random() * 0.72)
        : this.vw * Math.random() * 0.12;
      y = fromTop
        ? this.vh * Math.random() * 0.10
        : this.vh * (0.15 + Math.random() * 0.40);
    } else {
      angleDeg = 29 + Math.random() * 7;
      x = this.vw * (0.16 + Math.random() * 0.56);
      y = this.vh * (0.02 + Math.random() * 0.12);
    }
    const angle = (angleDeg * Math.PI) / 180;
    const speed = 300 + Math.random() * 80;
    // 尾迹长度按 20% 短 / 60% 中 / 20% 长分布，避免每颗都一样
    const trailRoll = Math.random();
    const trailLen = trailRoll < 0.2
      ? 140 + Math.random() * 40
      : trailRoll < 0.8
        ? 190 + Math.random() * 50
        : 250 + Math.random() * 30;
    target.push({
      x,
      y,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
      age: 0,
      life: 1.7 + Math.random() * 0.5,
      g: 4 + Math.random() * 5,
      drag: 0.02 + Math.random() * 0.02,
      trailLen,
      trail: [],
      spin: Math.random() * Math.PI * 2,
      spinSpeed: 0.05 + Math.random() * 0.07,
      breathPeriod: BREATH_PERIOD_MIN + Math.random() * (BREATH_PERIOD_MAX - BREATH_PERIOD_MIN),
      breathPhase: Math.random() * Math.PI * 2,
    });
    this.start();
  }

  start() {
    this.enabled = true;
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    this.raf = requestAnimationFrame((t) => this.frame(t));
  }

  stop() {
    this.enabled = false;
    this.list = [];
    this.groupList = [];
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.vw, this.vh);
    if (this.wctx) {
      this.wctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.wctx.clearRect(0, 0, this.vw, this.waterH);
    }
  }

  frame(now) {
    const dt = Math.min((now - this.last) / 1000, 0.05);
    this.last = now;
    this.step(dt);
    this.paint();
    if (this.list.length || this.groupList.length) {
      this.raf = requestAnimationFrame((t) => this.frame(t));
    } else {
      this.running = false;
      this.raf = 0;
    }
  }

  step(dt) {
    this.stepList(this.list, dt);
    this.stepList(this.groupList, dt);
    this.list = this.list.filter(
      (m) => m.age < m.life && m.x < this.vw + 300 && m.y < this.horizon,
    );
    this.groupList = this.groupList.filter(
      (m) => m.age < m.life && m.x < this.vw + 300 && m.y < this.horizon,
    );
  }

  stepList(list, dt) {
    for (const m of list) {
      m.age += dt;
      const damp = 1 - m.drag * dt;
      m.vx *= damp;
      m.vy = m.vy * damp + m.g * dt;
      m.x += m.vx * dt;
      m.y += m.vy * dt;
      m.trail.push({ x: m.x, y: m.y });
      // 尾迹只保留最近 trailLen 像素
      let acc = 0;
      let cut = 0;
      for (let i = m.trail.length - 1; i > 0; i -= 1) {
        acc += Math.hypot(
          m.trail[i].x - m.trail[i - 1].x,
          m.trail[i].y - m.trail[i - 1].y,
        );
        if (acc > m.trailLen) {
          cut = i;
          break;
        }
      }
      if (cut > 0) m.trail.splice(0, cut);
    }
  }

  paint() {
    const ctx = this.ctx;
    const phase = huePhase();
    const solo = this.prepareMeteors(this.list, phase);
    const group = this.prepareMeteors(this.groupList, phase);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.vw, this.vh);
    ctx.globalCompositeOperation = "lighter";
    this.paintPrepared(ctx, 1, solo, phase);
    this.paintPrepared(ctx, 1, group, phase);

    if (this.wctx && this.waterH > 0) {
      const w = this.wctx;
      const ratio = this.waterH / Math.max(this.horizon, 1);
      w.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      w.clearRect(0, 0, this.vw, this.waterH);
      w.setTransform(this.dpr, 0, 0, -this.dpr * ratio, 0, this.dpr * this.waterH);
      w.globalCompositeOperation = "lighter";
      this.paintPrepared(w, 0.4, solo, phase);
      this.paintPrepared(w, 0.4, group, phase);
    }

    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
    if (this.wctx) {
      this.wctx.globalAlpha = 1;
      this.wctx.globalCompositeOperation = "source-over";
    }
  }

  prepareMeteors(list, phase) {
    const target = hslToRgb(
      HUE_BASE_HSL.h + phase * 360,
      HUE_BASE_HSL.s / 100,
      HUE_BASE_HSL.l / 100,
    );
    return list.map((m) => this.prepareMeteor(m, target));
  }

  prepareMeteor(m, target) {
    const fade = Math.min(1, m.age / 0.08)
      * Math.min(1, Math.max(0, (m.life - m.age) / 0.3));
    const breath = 0.5 + 0.5 * Math.sin(
      (2 * Math.PI * m.age) / m.breathPeriod + m.breathPhase,
    );
    const sizeBreath = 0.5 + 0.5 * Math.sin(
      (2 * Math.PI * m.age) / m.breathPeriod + m.breathPhase + BREATH_SIZE_LAG,
    );

    // 按 6px 目标间距重采样轨迹，保留真实路径
    const src = m.trail;
    const points = [];
    for (let i = 0; i < src.length; i += 1) {
      if (i > 0) {
        const a = src[i - 1];
        const b = src[i];
        const d = Math.hypot(b.x - a.x, b.y - a.y);
        const steps = Math.max(1, Math.ceil(d / 6));
        for (let k = 1; k < steps; k += 1) {
          const t = k / steps;
          points.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
        }
      }
      points.push({ x: src[i].x, y: src[i].y });
    }

    const dist = [0];
    for (let i = 1; i < points.length; i += 1) {
      dist.push(dist[i - 1] + Math.hypot(
        points[i].x - points[i - 1].x,
        points[i].y - points[i - 1].y,
      ));
    }
    const total = dist[dist.length - 1] || 0;
    const head = points.length ? points[points.length - 1] : { x: m.x, y: m.y };
    const headWeight = this.weightAt(head.x, head.y);

    for (let i = 0; i < points.length; i += 1) {
      const p = total ? dist[i] / total : 0;
      const w = this.weightAt(points[i].x, points[i].y);
      const color = w > 0 ? mixRgb(METEOR_TINT, target, w) : METEOR_TINT;
      const hue = rgbToHue(color);
      points[i].p = p;
      points[i].color = color;
      points[i].bucket = Math.round(hue / HUE_BUCKET_DEG);
      // 地平线淡出按采样点各自算：头部沉下去时，仍在天上的尾迹不跟着一起淡
      points[i].horizon = horizonFadeAt(this.horizon, points[i].y);
    }
    const headHorizon = points.length
      ? points[points.length - 1].horizon
      : horizonFadeAt(this.horizon, head.y);

    let coreStart = points.length - 1;
    let fromHead = 0;
    for (let i = points.length - 1; i > 0; i -= 1) {
      fromHead += Math.hypot(
        points[i].x - points[i - 1].x,
        points[i].y - points[i - 1].y,
      );
      coreStart = i - 1;
      if (fromHead >= m.trailLen * 0.40) break;
    }

    const runs = [];
    if (points.length > 1) {
      let start = 0;
      let runColor = points[0].color;
      for (let i = 1; i < points.length; i += 1) {
        const prev = points[i - 1];
        const cur = points[i];
        const split = cur.bucket !== prev.bucket
          || maxChannelDiff(cur.color, prev.color) > HUE_RGB_LIMIT
          || maxChannelDiff(cur.color, runColor) > HUE_RGB_LIMIT
          || Math.abs(cur.horizon - points[start].horizon) > HORIZON_RUN_STEP
          || (prev.p < 0.55 && cur.p >= 0.55);
        if (split) {
          runs.push({ start, end: i - 1, color: runColor });
          start = i - 1;
          runColor = cur.color;
        }
      }
      if (start < points.length - 1) {
        runs.push({ start, end: points.length - 1, color: runColor });
      }
    }

    return {
      points,
      runs,
      coreStart,
      head,
      headWeight,
      fade,
      headFade: fade * headHorizon,
      haloRadius: HALO_RADIUS_MIN + (HALO_RADIUS_MAX - HALO_RADIUS_MIN) * sizeBreath,
      haloAlpha: HALO_ALPHA_MIN + (HALO_ALPHA_MAX - HALO_ALPHA_MIN) * breath,
      starSize: STAR_SIZE_MIN + (STAR_SIZE_MAX - STAR_SIZE_MIN) * sizeBreath,
      starAlpha: STAR_ALPHA_MIN + (STAR_ALPHA_MAX - STAR_ALPHA_MIN) * breath,
      spin: m.spin + m.age * m.spinSpeed,
    };
  }

  paintPrepared(ctx, alphaScale, prepared, phase) {
    ctx.lineCap = "butt";
    ctx.lineJoin = "round";
    for (const p of prepared) {
      ctx.globalAlpha = 1;

      // 外层柔光
      for (const run of p.runs) {
        this.strokeRun(
          ctx, p.points, run, run.color,
          p.fade * alphaScale * 0.16 * p.points[run.start].p * p.points[run.start].horizon,
          p.fade * alphaScale * 0.16 * p.points[run.end].p * p.points[run.end].horizon,
          4.5,
        );
      }

      // 青碧主体
      for (const run of p.runs) {
        this.strokeRun(
          ctx, p.points, run, run.color,
          p.fade * alphaScale * bodyAlpha(p.points[run.start].p) * p.points[run.start].horizon,
          p.fade * alphaScale * bodyAlpha(p.points[run.end].p) * p.points[run.end].horizon,
          1.8,
        );
      }

      // 白青亮芯：只覆盖头部约 40%，保持白色
      const last = p.points.length - 1;
      if (p.coreStart < last) {
        this.strokeRun(
          ctx, p.points, { start: p.coreStart, end: last }, [255, 255, 255],
          0, p.fade * alphaScale * 0.85 * p.points[last].horizon, 0.9,
        );
      }

      // 头部柔光：按头部位置取色，量化后走贴图缓存
      ctx.globalAlpha = p.headFade * p.haloAlpha * alphaScale;
      const radius = p.haloRadius;
      ctx.drawImage(
        glowSprite(glowTint(p.headWeight, phase)),
        p.head.x - radius, p.head.y - radius, radius * 2, radius * 2,
      );

      // 十字星芒保持原样
      if (starReady) {
        ctx.globalAlpha = p.headFade * p.starAlpha * alphaScale;
        ctx.save();
        ctx.translate(p.head.x, p.head.y);
        ctx.rotate(p.spin);
        ctx.drawImage(starTinted, -p.starSize / 2, -p.starSize / 2, p.starSize, p.starSize);
        ctx.restore();
      }
    }
    ctx.globalAlpha = 1;
  }

  strokeRun(ctx, points, run, rgb, alphaStart, alphaEnd, width) {
    if (run.end <= run.start) return;
    const start = points[run.start];
    const end = points[run.end];
    const grad = ctx.createLinearGradient(start.x, start.y, end.x, end.y);
    const a0 = Math.max(0, Math.min(1, alphaStart));
    const a1 = Math.max(0, Math.min(1, alphaEnd));
    grad.addColorStop(0, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a0})`);
    grad.addColorStop(1, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a1})`);
    ctx.strokeStyle = grad;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    for (let i = run.start + 1; i <= run.end; i += 1) {
      ctx.lineTo(points[i].x, points[i].y);
    }
    ctx.stroke();
  }
}

let nearField = null;
let nearTimer = 0;
let nearGroupTimer = 0;
let nearGroupTimers = [];
// 单颗流星的 15% 补发：单独一把计时器，不和群组的 reset 混用，
// 否则群组下一次重置会把还没触发的补发句柄丢掉、停不下来
let nearSpawnTimers = [];
let farEnabled = false;
let farTimer = 0;

function initNearMeteors() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const sky = $("meteor-canvas");
  const water = $("meteor-water");
  if (!sky || !water) return;
  if (!nearField) {
    nearField = new NearMeteors(sky, water);
  } else {
    nearField.enabled = true;
  }
}

function stopNearMeteors() {
  clearTimeout(nearTimer);
  nearTimer = 0;
  clearTimeout(nearGroupTimer);
  nearGroupTimer = 0;
  for (const timer of nearGroupTimers) clearTimeout(timer);
  nearGroupTimers = [];
  for (const timer of nearSpawnTimers) clearTimeout(timer);
  nearSpawnTimers = [];
  if (nearField) nearField.stop();
}

function startFarMeteors() {
  if (farEnabled) return;
  farEnabled = true;
  scheduleMeteor(false);
}

function stopFarMeteors() {
  farEnabled = false;
  clearTimeout(farTimer);
  farTimer = 0;
}

function scheduleMeteor(near) {
  if (near && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (!near && !farEnabled) return;
  // 远景保持 0.8–1.9 秒的频率（30% 连发）；近景单颗 6–12 秒一颗，最多 2 颗。
  const delay = near
    ? 6000 + Math.random() * 6000
    : 800 + Math.random() * 1100;
  const timer = setTimeout(() => {
    if (near) {
      if (nearField) {
        nearField.spawn();
        if (Math.random() < 0.15) {
          const timer = setTimeout(() => {
            nearSpawnTimers = nearSpawnTimers.filter((t) => t !== timer);
            if (nearField) nearField.spawn();
          }, 400 + Math.random() * 400);
          nearSpawnTimers.push(timer);
        }
      }
    } else {
      if (!farEnabled) return;
      const bursts = Math.random() < 0.3 ? 2 : 1;
      for (let i = 0; i < bursts; i += 1) {
        setTimeout(() => spawnMeteor(), i * (140 + Math.random() * 220));
      }
    }
    scheduleMeteor(near);
  }, delay);
  if (near) nearTimer = timer;
  else farTimer = timer;
}

// 流星群：与单颗近景流星互不影响，独立计时、独立上限 3 颗
function scheduleGroupMeteor() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const delay = 7000 + Math.random() * 3000;
  const timer = setTimeout(() => {
    if (nearField) {
      // 65% 两颗、35% 三颗；组内间隔 180–320ms，同组共用一个基础角度
      const count = Math.random() < 0.35 ? 3 : 2;
      const baseAngle = 33 + (Math.random() * 3 - 1.5);
      nearGroupTimers = [];
      let at = 0;
      for (let i = 0; i < count; i += 1) {
        if (i === 0) {
          nearField.spawn({ group: true, baseAngle });
        } else {
          at += 180 + Math.random() * 140;
          nearGroupTimers.push(setTimeout(() => {
            if (nearField) nearField.spawn({ group: true, baseAngle });
          }, at));
        }
      }
    }
    scheduleGroupMeteor();
  }, delay);
  nearGroupTimer = timer;
}

// 地平线在视口里的位置：与背景 cover 的缩放/居中保持一致，倒影层据此翻转
function layoutBackdrop() {
  const horizon = 2014;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const { scale, offsetY } = backdropMetrics(vw, vh);
  const hy = offsetY + horizon * scale;
  document.documentElement.style.setProperty("--horizon-y", `${hy.toFixed(1)}px`);
}

// 星尘：不落，只在原地附近缓慢漂荡，明暗各自呼吸
function initDust() {
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

// 风：几道横风带从左侧扫过，配合被吹斜的雪
function initWind() {
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

// 风雪：雪片整体被风往右下吹，途中沿风向摆动
function initSnow() {
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

function initSky() {
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
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (!reduced.matches) {
    startFarMeteors();
    spawnMeteor();
    initNearMeteors();
    scheduleMeteor(true);
    scheduleGroupMeteor();
  }
  reduced.addEventListener("change", (event) => {
    if (event.matches) {
      stopNearMeteors();
      stopFarMeteors();
    } else {
      startFarMeteors();
      spawnMeteor();
      initNearMeteors();
      scheduleMeteor(true);
      scheduleGroupMeteor();
    }
  });
}

// ---- 牌面缩略图（bridge 只回 JSON，图片走 base64） ----
const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    observer.unobserve(entry.target);
    loadThumb(entry.target);
  }
}, { rootMargin: "160px" });

async function thumbURL(cardId, upright, width) {
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

async function ensureCardBack(width = 150) {
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
function loadThumb(img) {
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

function makeThumb(cardId, upright, width, alt) {
  const img = el("img", "thumb");
  img.alt = alt || "";
  img.loading = "lazy";
  img.dataset.cardId = cardId;
  img.dataset.upright = upright ? "1" : "0";
  img.dataset.width = String(width);
  observer.observe(img);
  return img;
}

// ---- 概览 ----
const SEND_MODES = { forward: "合并转发", plain: "图文", text_only: "纯文字" };
// 人设取值见 core/settings.py 的 PERSONA_VALUES，这里只负责显示成中文
const PERSONA_NAMES = {
  off: "中立", tsundere: "傲娇", gentle: "温柔", mystic: "神秘", random: "随机",
};

function renderStats(data) {
  const box = $("stats");
  box.replaceChildren();
  const settings = data.settings || {};
  const chips = [
    ["今日用户", data.users],
    ["主题解读", data.topics],
    ["牌灵话", data.with_spirit],
    ["缓存过期", data.stale, data.stale > 0 ? "warn" : ""],
    ["AI", settings.enable_ai ? "开" : "关"],
    ["结构校验", settings.structure_check],
    ["发送", SEND_MODES[settings.send_mode] || settings.send_mode],
  ];
  for (const [label, value, cls] of chips) {
    const chip = el("div", `chip ${cls || ""}`);
    chip.append(el("span", "chip-label", label), el("strong", "chip-value", String(value)));
    box.append(chip);
  }
}

// ---- 今日牌运 ----
function renderDailyList() {
  const list = $("daily-list");
  list.replaceChildren();
  const note = $("daily-note");
  if (state.listTruncated) {
    note.textContent = `当前索引覆盖 ${state.listIndexedTotal} 位用户，`
      + `页面显示最近 ${state.listShown} 位`;
    note.classList.remove("hidden");
  } else {
    note.textContent = "";
    note.classList.add("hidden");
  }
  if (!state.items.length) {
    list.append(el("p", "empty", "今天还没有牌运记录。抽一签后这里会出现。"));
    return;
  }
  for (const item of state.items) {
    const button = el("button", "user" + (item.uid === state.selected ? " active" : ""));
    button.type = "button";
    button.append(makeThumb(item.card_id, item.upright, 84, item.card_cn));

    const body = el("div", "user-body");
    const top = el("div", "user-top");
    top.append(el("strong", "user-card", item.card_cn));
    top.append(el("span", "tag " + (item.upright ? "up" : "rev"),
                  item.upright ? "正位" : "逆位"));
    if (item.stale) top.append(el("span", "tag stale", "缓存过期"));
    body.append(top);
    body.append(el("span", "user-uid", item.uid));
    body.append(el("span", "user-spirit", item.spirit || "（今天还没有牌灵的话）"));
    body.append(el("span", "user-meta", `${item.topics} 个主题解读`));
    button.append(body);

    button.addEventListener("click", () => openDaily(item.uid));
    list.append(button);
  }
}

async function openDaily(uid) {
  state.selected = uid;
  const seq = ++detailSeq;
  renderDailyList();
  const box = $("daily-detail");
  box.replaceChildren(el("p", "empty", "读取中…"));
  try {
    const data = await apiGet("daily/detail", { uid });
    if (seq !== detailSeq || state.selected !== uid) return;
    if (!data) throw new Error("没有这条记录");
    renderDetail(data);
  } catch (error) {
    if (seq !== detailSeq || state.selected !== uid) return;
    box.replaceChildren(el("p", "empty", error.message || "读取失败"));
  }
}

function renderDetail(data) {
  const box = $("daily-detail");
  box.replaceChildren();

  const head = el("div", "detail-head");
  const figure = el("div", "detail-card");
  figure.append(makeThumb(data.card.id, data.upright, 220, data.card.cn));
  head.append(figure);

  const info = el("div", "detail-info");
  const titleRow = el("div", "detail-title");
  titleRow.append(el("h2", "", data.card.cn));
  titleRow.append(el("span", "tag " + (data.upright ? "up" : "rev"),
                     data.upright ? "正位" : "逆位"));
  if (data.stale) titleRow.append(el("span", "tag stale", "缓存过期"));
  info.append(titleRow);
  info.append(el("p", "detail-uid", data.uid));
  info.append(el("p", "detail-meta",
                 `日期 ${data.date} · 人设 ${PERSONA_NAMES[data.persona] || data.persona || "默认"}`));
  if (data.spirit) {
    const spirit = el("blockquote", "detail-spirit");
    spirit.append(el("span", "quote-mark", "“"));
    spirit.append(el("span", "", data.spirit));
    info.append(spirit);
  }
  head.append(info);
  box.append(head);

  if (!data.topics.length) {
    box.append(el("p", "empty", "今天还没有生成解读。"));
    return;
  }
  for (const topic of data.topics) {
    const section = el("section", "topic");
    section.append(el("h3", "topic-title", topic.topic));
    section.append(el("p", "topic-text", topic.text || topic.summary || "（空解读）"));
    box.append(section);
  }
}

// ---- 牌面预览 ----
async function loadCards() {
  if (state.cards.length) {
    renderCards();
    return;
  }
  try {
    const data = await apiGet("cards");
    state.cards = data.items || [];
    renderCards();
  } catch (error) {
    showToast(error.message || "牌面清单加载失败");
  }
}

function renderCards() {
  const grid = $("card-grid");
  grid.replaceChildren();
  for (const card of state.cards) {
    const cell = el("button", "card-cell");
    cell.type = "button";
    cell.append(makeThumb(card.id, !state.reversed, 150, card.cn));
    cell.append(el("span", "card-name", card.cn));
    cell.addEventListener("click", () => openCardDetail(card));
    grid.append(cell);
  }
}

let cardDetailSeq = 0;
let cardImageSeq = 0;

async function openCardDetail(card) {
  const seq = ++cardDetailSeq;
  try {
    const detail = await apiGet("card-detail", { id: card.id });
    if (seq !== cardDetailSeq) return;  // 已被更新的点击或关闭取代
    state.cardDetail = detail;
    state.detailUpright = !state.reversed;
    $("card-modal").classList.remove("hidden");
    renderCardDetail();
  } catch (error) {
    if (seq !== cardDetailSeq) return;
    showToast(error.message || "牌义加载失败");
  }
}

function renderCardDetail() {
  const detail = state.cardDetail;
  if (!detail) return;
  const upright = state.detailUpright;
  const part = (upright ? detail.up : detail.down) || {};

  $("card-modal-name").textContent = detail.cn;
  $("card-modal-en").textContent = detail.en || "";
  const suit = SUIT_NAMES[detail.suit] || detail.suit || "";
  $("card-modal-meta").textContent = `${suit}${detail.num ? ` · ${detail.num}` : ""}`;

  for (const button of $("card-orient").querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.upright === (upright ? "1" : "0"));
  }

  const imageSeq = ++cardImageSeq;
  const img = $("card-modal-img");
  img.alt = detail.cn;
  img.removeAttribute("src");
  thumbURL(detail.id, upright, 240).then((url) => {
    if (imageSeq !== cardImageSeq) return;  // 已被新的牌/朝向/关闭取代
    if (url) img.src = url;
  }).catch(() => {
    if (imageSeq === cardImageSeq) showToast("牌面加载失败");
  });

  const body = $("card-modal-body");
  body.replaceChildren();
  const keywords = Array.isArray(part.keywords) ? part.keywords : [];
  if (keywords.length) {
    const row = el("div", "kw-row");
    for (const keyword of keywords) row.append(el("span", "kw", String(keyword)));
    body.append(row);
  }
  const rows = upright
    ? [["牌义", part.what], ["建议", part.how]]
    : [["牌义", part.what], ["提醒", part.turn], ["能量", part.energy]];
  for (const [label, text] of rows) {
    if (!text) continue;
    const section = el("section", "meaning");
    section.append(el("dt", "meaning-label", label));
    section.append(el("dd", "meaning-text", String(text)));
    body.append(section);
  }
}

function closeCardDetail() {
  cardDetailSeq += 1;  // 关掉后，未返回的详情请求作废
  cardImageSeq += 1;   // 未返回的缩略图请求同样作废
  $("card-modal").classList.add("hidden");
  state.cardDetail = null;
}

// ---- 牌阵介绍 ----
async function ensureSpreads() {
  if (!state.spreads.length) {
    try {
      const data = await apiGet("spreads");
      state.spreads = data.items || [];
    } catch (error) {
      showToast(error.message || "牌阵清单加载失败");
    }
  }
  return state.spreads;
}

async function loadSpreads() {
  await ensureSpreads();
  renderSpreads();
}

// ---- 占卜预览：本地抽牌，AI 解读只在点击后请求 ----
let previewBusy = false;
let previewAiSeq = 0;
let previewSeq = 0;

async function loadPreviewForm() {
  const spreads = await ensureSpreads();
  const select = $("preview-spread");
  if (select.dataset.ready === "1") return;
  if (!spreads.length) {
    // 加载失败时不标记 ready，下次进标签页可以重试
    showToast("牌阵清单加载失败，稍后再试");
    return;
  }
  for (const spread of spreads) {
    const option = el("option", "", `${spread.name} · ${spread.positions.length} 张`);
    option.value = spread.name;
    select.append(option);
  }
  select.dataset.ready = "1";
  ensureCardBack().catch(() => {});
}

async function drawPreview() {
  if (previewBusy) return;
  const spread = $("preview-spread").value;
  if (!spread) return;
  const seq = ++previewSeq;
  previewBusy = true;
  previewAiSeq += 1;  // 抽牌一开始就作废上一轮 AI，不必等抽牌结果返回
  $("preview-draw").disabled = true;
  $("preview-ai").disabled = true;
  try {
    const question = $("preview-question").value.trim();
    const data = await apiPost("preview/draw", { spread, question });
    if (seq !== previewSeq) return;
    state.preview = {
      spread: data.spread,
      question: data.question || "",
      positions: data.positions || [],
      picks: data.picks || [],
      ai: null,
      aiError: "",
    };
    renderPreview({ animate: true });
  } catch (error) {
    if (seq === previewSeq) showToast(error.message || "抽牌失败");
  } finally {
    if (seq === previewSeq) {
      previewBusy = false;
      $("preview-draw").disabled = false;
      updatePreviewAIButton();
    }
  }
}

function resetPreview() {
  previewSeq += 1;
  previewAiSeq += 1;
  previewBusy = false;
  state.preview = null;
  const draw = $("preview-draw");
  const ai = $("preview-ai");
  if (draw) draw.disabled = false;
  if (ai) {
    ai.disabled = true;
    ai.textContent = "AI 深度解读";
  }
  renderPreview();
  updatePreviewAIButton();
}

async function requestPreviewAI() {
  const preview = state.preview;
  if (!preview || !preview.picks.length) return;
  const seq = ++previewAiSeq;
  const button = $("preview-ai");
  button.disabled = true;
  button.textContent = "解读中…";
  preview.aiError = "";
  try {
    const data = await apiPost("preview/interpret", {
      spread: preview.spread,
      question: preview.question,
      picks: preview.picks.map((pick) => ({ id: pick.id, upright: pick.upright })),
    });
    if (seq !== previewAiSeq || state.preview !== preview) return;
    preview.ai = data;
  } catch (error) {
    if (seq !== previewAiSeq || state.preview !== preview) return;
    preview.aiError = error.message || "AI 解读失败";
  } finally {
    if (seq === previewAiSeq) {
      button.textContent = "AI 深度解读";
      updateAiBlock({ animate: true });
      updatePreviewAIButton();
    }
  }
}

function updatePreviewAIButton() {
  const button = $("preview-ai");
  if (!button) return;
  const ready = Boolean(state.preview && state.preview.picks.length) && !previewBusy;
  button.disabled = !ready || !state.aiEnabled;
  button.title = state.aiEnabled ? "" : "AI 深度解读已在配置中关闭";
  $("preview-note").textContent = state.aiEnabled
    ? "预览不消耗次数、不写入缓存、不会发送到聊天"
    : "AI 已关闭，预览只显示内置牌义；不消耗次数、不写入缓存、不会发送到聊天";
}

function renderPreview({ animate = false } = {}) {
  const box = $("preview-result");
  box.replaceChildren();
  const preview = state.preview;
  if (!preview || !preview.picks.length) {
    box.append(el("p", "empty", "选一个牌阵，抽一次牌。"));
    return;
  }

  const head = el("div", "preview-head");
  head.append(el("h2", "", preview.spread));
  head.append(el("span", "tag", `${preview.picks.length} 张`));
  if (preview.question) head.append(el("span", "preview-question", preview.question));
  box.append(head);

  const grid = el("div", "preview-cards");
  preview.picks.forEach((pick, index) => {
    const card = el("article", "pick-card");
    if (animate) {
      card.classList.add("flip-animate");
      card.style.setProperty("--flip-delay", `${index * 130}ms`);
    }
    const figure = el("div", "pick-figure");
    const thumb = el("div", "pick-thumb");
    const flip = el("div", "pick-flip");
    const back = el("div", "pick-face pick-back");
    if (cardBackURL) back.style.backgroundImage = `url("${cardBackURL}")`;
    const front = el("div", "pick-face pick-front");
    front.append(makeThumb(pick.id, pick.upright, 150, pick.cn));
    flip.append(back, front);
    thumb.append(flip);
    if (animate) {
      if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        card.classList.add("card-flipped");
      } else {
        front.addEventListener("animationend", () => card.classList.add("card-flipped"), { once: true });
      }
    }
    figure.append(thumb);
    figure.append(el("span", "pick-pos",
                     `${index + 1} · ${preview.positions[index] || ""}`));
    card.append(figure);

    const body = el("div", "pick-body");
    const title = el("div", "pick-title");
    title.append(el("strong", "", pick.cn));
    title.append(el("span", "tag " + (pick.upright ? "up" : "rev"),
                    pick.upright ? "正位" : "逆位"));
    body.append(title);
    if (Array.isArray(pick.keywords) && pick.keywords.length) {
      const row = el("div", "kw-row");
      for (const word of pick.keywords) row.append(el("span", "kw", String(word)));
      body.append(row);
    }
    if (pick.what) body.append(el("p", "pick-text", pick.what));
    if (pick.advice) body.append(el("p", "pick-advice", pick.advice));
    if (pick.energy) body.append(el("p", "pick-energy", pick.energy));
    card.append(body);
    grid.append(card);
  });
  box.append(grid);
  box.append(buildAiBlock(preview, false));
  if (animate) {
    // 等所有正面和牌背都就绪后再统一起跳：否则先 decode 完的牌会抢先翻，
    // 顺序和间隔都会随图片加载时间漂移。
    const fronts = [...grid.querySelectorAll(".pick-front .thumb")];
    Promise.all(fronts.map((img) => loadThumb(img)))
      .then(() => ensureCardBack())
      .then((url) => {
        if (url) {
          box.querySelectorAll(".pick-back").forEach((node) => {
            node.style.backgroundImage = `url("${url}")`;
          });
        }
        grid.classList.add("flips-ready");
      })
      .catch(() => {
        grid.classList.add("flips-ready");
      });
  }
}

function buildAiBlock(preview, animate = false) {
  const ai = el("section", "ai-result");
  if (animate) ai.classList.add("ai-reveal");
  if (preview.ai) {
    ai.append(el("h3", "", "AI 深度解读"));
    ai.append(el("p", "ai-text", preview.ai.text || ""));
  } else if (preview.aiError) {
    ai.append(el("p", "empty", preview.aiError));
  } else {
    ai.append(el("p", "hint", state.aiEnabled
      ? "点上方「AI 深度解读」获取结构化解读。"
      : "AI 深度解读已在配置中关闭，以上是内置牌义。"));
  }
  return ai;
}

function updateAiBlock({ animate = false } = {}) {
  const box = $("preview-result");
  const old = box.querySelector(".ai-result");
  if (!old || !state.preview) return;
  old.replaceWith(buildAiBlock(state.preview, animate));
}

function renderSpreads() {
  const box = $("spread-grid");
  box.replaceChildren();
  for (const spread of state.spreads) {
    const card = el("article", "spread-card");
    const head = el("div", "spread-head");
    head.append(el("h3", "", spread.name));
    head.append(el("span", "tag", `${spread.positions.length} 张`));
    card.append(head);
    if (spread.scenario) card.append(el("p", "spread-scenario", spread.scenario));

    const layout = el("div", "spread-layout");
    spread.positions.forEach((position, index) => {
      const slot = el("div", "spread-slot");
      slot.append(el("span", "spread-num", String(index + 1)));
      slot.append(el("strong", "spread-pos", position.name));
      if (position.meaning) slot.append(el("span", "spread-mean", position.meaning));
      layout.append(slot);
    });
    card.append(layout);
    box.append(card);
  }
}

let lightboxSeq = 0;

async function openLightbox(card, upright = !state.reversed) {
  const seq = ++lightboxSeq;
  const box = $("lightbox");
  const img = $("lightbox-img");
  img.removeAttribute("src");
  img.alt = card.cn;
  box.classList.remove("hidden");
  img.dataset.loaded = "";
  try {
    const url = await thumbURL(card.id, upright, 420);
    if (seq !== lightboxSeq) return;  // 已关闭或已换牌
    if (url) img.src = url;
  } catch {
    if (seq === lightboxSeq) showToast("大图加载失败");
  }
}

function closeLightbox() {
  lightboxSeq += 1;  // 关闭后，未返回的大图请求作废
  $("lightbox").classList.add("hidden");
}

// ---- 装配 ----
function switchTab(tab) {
  for (const button of document.querySelectorAll(".tab")) {
    button.classList.toggle("active", button.dataset.tab === tab);
  }
  $("panel-daily").classList.toggle("hidden", tab !== "daily");
  $("panel-cards").classList.toggle("hidden", tab !== "cards");
  $("panel-spreads").classList.toggle("hidden", tab !== "spreads");
  $("panel-preview").classList.toggle("hidden", tab !== "preview");
  if (tab === "cards") loadCards();
  if (tab === "spreads") loadSpreads();
  if (tab === "preview") loadPreviewForm();
}

async function refresh() {
  if (!$("panel-preview").classList.contains("hidden")) resetPreview();
  try {
    const overview = await apiGet("overview");
    state.overview = overview;
    state.aiEnabled = Boolean(overview.settings && overview.settings.enable_ai);
    $("version").textContent = overview.version ? `v${overview.version}` : "";
    renderStats(overview);
    updatePreviewAIButton();
  } catch (error) {
    showToast(error.message || "概览加载失败");
  }
  try {
    const data = await apiGet("daily/list");
    state.items = data.items || [];
    state.listIndexedTotal = Number.isFinite(data.indexed_total)
      ? data.indexed_total : state.items.length;
    state.listShown = Number.isFinite(data.shown) ? data.shown : state.items.length;
    state.listTruncated = Boolean(data.truncated);
    const stillSelected = Boolean(state.selected)
      && state.items.some((item) => item.uid === state.selected);
    renderDailyList();
    if (stillSelected) {
      await openDaily(state.selected);  // 记录可能已变，刷新时重拉详情
    } else if (state.items.length) {
      await openDaily(state.items[0].uid);
    } else {
      state.selected = null;
      renderDailyList();
      $("daily-detail").replaceChildren(el("p", "empty", "今天还没有牌运记录。"));
    }
  } catch (error) {
    showToast(error.message || "牌运列表加载失败");
  }
}

async function boot() {
  if (!isDemo) {
    try {
      const title = await connect();
      $("heading").textContent = title;
      document.title = title;
    } catch {
      /* bridge 上下文拿不到也不影响只读数据 */
    }
  } else {
    $("subtitle").textContent = "预览模式 · 未连接 AstrBot";
    $("subtitle").classList.add("demo");
  }
  $("version").textContent = "";
  layoutBackdrop();
  initSky();
  initDust();
  initWind();
  initSnow();
  window.addEventListener("resize", () => {
    layoutBackdrop();
    if (nearField) nearField.resize();
  });
  for (const button of document.querySelectorAll(".tab")) {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  }
  $("refresh").addEventListener("click", refresh);
  $("reversed").addEventListener("change", (event) => {
    state.reversed = event.target.checked;
    renderCards();
  });
  $("lightbox").addEventListener("click", closeLightbox);
  $("preview-draw").addEventListener("click", drawPreview);
  $("preview-ai").addEventListener("click", requestPreviewAI);
  $("preview-question").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      drawPreview();
    }
  });
  $("card-modal-close").addEventListener("click", closeCardDetail);
  $("card-modal").addEventListener("click", (event) => {
    if (event.target === $("card-modal")) closeCardDetail();
  });
  $("card-modal-img").addEventListener("click", () => {
    if (state.cardDetail) openLightbox(state.cardDetail, state.detailUpright);
  });
  for (const button of $("card-orient").querySelectorAll("button")) {
    button.addEventListener("click", () => {
      state.detailUpright = button.dataset.upright === "1";
      renderCardDetail();
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeLightbox();
      closeCardDetail();
    }
  });
  await refresh();
}

boot();
