import { backdropMetrics } from "./geometry.js";

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

// 这里只决定色相相位取值，不是启停判断；启停统一由 background/index.js 转交。
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
// 本模块在 feathers/background/ 下，素材仍在视图根目录的 assets/
starSprite.src = new URL("../../assets/star.webp", import.meta.url).href;

// 近流星：canvas 渲染。按「重力 + 阻力」逐帧积分，尾迹沿真实路径撒柔光精灵
// （原场景 ropetrail 的做法：精灵重叠、加法混合累积出亮芯）。
// 生成、补发与连发都是模块内部调度，对外只给 start / stop / resize。
export class NearMeteors {
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
    this.soloTimer = 0;
    this.groupTimer = 0;
    this.groupTimers = [];
    // 单颗流星的 15% 补发：单独一把计时器，不和群组的 reset 混用，
    // 否则群组下一次重置会把还没触发的补发句柄丢掉、停不下来
    this.spawnTimers = [];
    this.scheduled = false;
    this.resize();
  }

  start() {
    this.enabled = true;
    this.startRaf();
    if (this.scheduled) return;
    this.scheduled = true;
    this.scheduleSolo();
    this.scheduleGroup();
  }

  stop() {
    this.enabled = false;
    this.scheduled = false;
    clearTimeout(this.soloTimer);
    this.soloTimer = 0;
    clearTimeout(this.groupTimer);
    this.groupTimer = 0;
    for (const timer of this.groupTimers) clearTimeout(timer);
    this.groupTimers = [];
    for (const timer of this.spawnTimers) clearTimeout(timer);
    this.spawnTimers = [];
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
    this.startRaf();
  }

  startRaf() {
    if (this.running) return;
    this.running = true;
    this.last = performance.now();
    this.raf = requestAnimationFrame((t) => this.frame(t));
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

  // 近景单颗：6–12 秒一颗，最多 2 颗；15% 概率补一颗
  scheduleSolo() {
    const delay = 6000 + Math.random() * 6000;
    this.soloTimer = setTimeout(() => {
      if (!this.enabled) return;
      this.spawn();
      if (Math.random() < 0.15) {
        const timer = setTimeout(() => {
          this.spawnTimers = this.spawnTimers.filter((t) => t !== timer);
          if (this.enabled) this.spawn();
        }, 400 + Math.random() * 400);
        this.spawnTimers.push(timer);
      }
      this.scheduleSolo();
    }, delay);
  }

  // 流星群：与单颗近景流星互不影响，独立计时、独立上限 3 颗
  scheduleGroup() {
    const delay = 7000 + Math.random() * 3000;
    this.groupTimer = setTimeout(() => {
      if (!this.enabled) return;
      // 65% 两颗、35% 三颗；组内间隔 180–320ms，同组共用一个基础角度
      const count = Math.random() < 0.35 ? 3 : 2;
      const baseAngle = 33 + (Math.random() * 3 - 1.5);
      this.groupTimers = [];
      let at = 0;
      for (let i = 0; i < count; i += 1) {
        if (i === 0) {
          this.spawn({ group: true, baseAngle });
        } else {
          at += 180 + Math.random() * 140;
          this.groupTimers.push(setTimeout(() => {
            if (this.enabled) this.spawn({ group: true, baseAngle });
          }, at));
        }
      }
      this.scheduleGroup();
    }, delay);
  }
}
