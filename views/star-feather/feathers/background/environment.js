// 环境粒子系统（PR1：只实现雪）。
// 风场状态、模拟时间与粒子都在这里；全局监听由 background/index.js 转交。
import { advanceGustState, createGustState, sampleWind } from "./wind-field.js";
import { drawDustSprite, drawGust, drawSnowSprite, ensureDustSprites, ensureGustSprite, ensureSnowSprites } from "./environment-sprites.js";

const STEP = 1 / 60;
const MAX_DELTA = 0.05;
const MAX_STEPS = 3;
const DPR_CAP = 1.5;
const TRAVEL_RATIO = 1.10; // 旧版 -6vh → 104vh
const HORIZON_FADE_PX = 60;
const GUST_PEAK = 1.7;

const SNOW = {
  count: 72,
  alphaFar: 0.20, alphaMid: 0.52, alphaNear: 0.85,
  driftFar: 20, driftMid: 32, driftNear: 55,
  responseMin: 0.65, responseMax: 0.95,
  inertia: 0.65,
  swayAmpMin: 12.8, swayAmpMax: 51.2,
  fadeInRatio: 0.08,
  fadeOutRatio: 0.10,
};

const DUST = {
  count: 56,
  sizeFar: 1.6, sizeMid: 2.8, sizeNear: 4.2,
  driftVw: 0.04, // 旧版 --dx ±4vw
  driftVh: 0.03, // 旧版 --dy ±3vh
  periodMin: 32, // alternate 一来一回
  periodMax: 80,
  responseMin: 0.25, responseMax: 0.55,
  inertia: 1.2,
  sinkMax: 0.5,
  twinkleMin: 2.6, twinkleMax: 6,
  alphaMin: 0.12, alphaMax: 0.9,
  scaleMin: 0.72, scaleMax: 1.12,
};

// 旧版 gust-sweep：-45vw → 125vw，6–12 s，18% 淡入 / 78% 后淡出，scaleX 0.65 → 1.2
const GUST = {
  count: 10,
  lengthVwMin: 0.24, lengthVwMax: 0.58,
  lifeMin: 6, lifeMax: 12,
  travelVw: 1.70,
  startVw: -0.45,
  opacityMin: 0.08, opacityMax: 0.22,
  angle: 1.6,
  yMinRatio: 0.04, yMaxRatio: 0.66,
};

// 旧版 dust.js：金 12%、青 12%、其余白
function dustColorIndex() {
  const roll = Math.random();
  if (roll < 0.12) return 1;
  if (roll < 0.24) return 2;
  return 0;
}

function lerp(a, b, t) {
  return a + (b - a) * t;
}

// 三档锚点分段线性：远 → 中 → 近
function segment(z, far, mid, near) {
  return z <= 0.5 ? lerp(far, mid, z / 0.5) : lerp(mid, near, (z - 0.5) / 0.5);
}

function snowSize(z) {
  return 1.4 + z * z * 4.4;
}

// 旧版 animationDuration = 16.5 - depth*8 + rand*2；下落速度由 110vh 反算
function snowDuration(z) {
  return 16.5 - z * 8 + Math.random() * 2;
}
function baseDriftFor(z) {
  return segment(z, SNOW.driftFar, SNOW.driftMid, SNOW.driftNear);
}

function alphaFor(z) {
  return segment(z, SNOW.alphaFar, SNOW.alphaMid, SNOW.alphaNear);
}

export class Environment {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.options = { wind: true, gust: true, seed: 0x5f3759df, ...options };
    this.particles = [];
    this.gusts = [];
    this.wind = { x: 1, y: 0, strength: 0, gust: 0 };
    this.sprites = ensureSnowSprites();
    this.dustSprites = ensureDustSprites();
    this.gustSprite = ensureGustSprite();
    this.dpr = 1;
    this.vw = 0;
    this.vh = 0;
    this.horizon = 0;
    this.running = false;
    this.raf = 0;
    this.lastTime = 0;
    this.acc = 0;
    this.simTime = 0;
    this.gustState = createGustState(this.options.seed, GUST_PEAK);
    this.resize();
    // PR1 验收用的只读调试入口；PR4 删除
    const params = new URLSearchParams(window.location.search);
    if (params.has("debug") || params.get("bg") === "debug") {
      window.__env = this;
    }
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.canvas.style.display = "";
    this.lastTime = performance.now();
    this.raf = requestAnimationFrame((t) => this.frame(t));
  }

  // 页面隐藏：停 RAF，保留粒子与画布
  pause() {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  // reduced-motion：停 RAF 并隐藏画布，粒子状态保留
  stop() {
    this.pause();
    this.canvas.style.display = "none";
  }

  destroy() {
    this.pause();
    this.particles = [];
    this.gusts = [];
    this.ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  resize() {
    const prevW = this.vw;
    const prevH = this.vh;
    this.dpr = Math.min(window.devicePixelRatio || 1, DPR_CAP);
    this.vw = window.innerWidth;
    this.vh = window.innerHeight;
    const hz = parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--horizon-y"),
    );
    this.horizon = Number.isFinite(hz) ? hz : this.vh * 0.7;

    this.canvas.width = Math.round(this.vw * this.dpr);
    this.canvas.height = Math.round(this.vh * this.dpr);
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    if (this.particles.length && prevW > 0 && prevH > 0) {
      const sx = this.vw / prevW;
      const sy = this.vh / prevH;
      for (const p of this.particles) {
        p.x *= sx;
        p.y *= sy;
        if (p.type === "dust") {
          p.driftAmpX = DUST.driftVw * this.vw;
          p.driftAmpY = DUST.driftVh * this.vh;
          continue;
        }
        // life（即旧版动画时长）不随视口变化；速度随 110vh 重算
        p.travel = TRAVEL_RATIO * this.vh;
        p.fallSpeed = p.travel / p.life;
        p.swayPeriod = 0.48 * p.life;
      }
      for (const g of this.gusts) {
        g.x *= sx;
        g.y *= sy;
        g.length *= sx;
        g.speed = (GUST.travelVw * this.vw) / g.life;
      }
    } else {
      this.spawnAll();
    }
    this.render();
  }

  spawnAll() {
    this.particles = [];
    for (let i = 0; i < SNOW.count; i += 1) this.particles.push(this.makeParticle());
    for (let i = 0; i < DUST.count; i += 1) this.particles.push(this.makeDust());
    // 远处的先画，近处的后画
    this.particles.sort((a, b) => a.z - b.z);

    this.gusts = [];
    for (let i = 0; i < GUST.count; i += 1) {
      const g = this.makeGust();
      g.age = Math.random() * g.life;
      g.x = (GUST.startVw + GUST.travelVw * (g.age / g.life)) * this.vw;
      this.gusts.push(g);
    }
  }

  makeGust() {
    const life = GUST.lifeMin + Math.random() * (GUST.lifeMax - GUST.lifeMin);
    const length = (GUST.lengthVwMin + Math.random() * (GUST.lengthVwMax - GUST.lengthVwMin)) * this.vw;
    return {
      type: "gust",
      life,
      age: 0,
      length,
      speed: (GUST.travelVw * this.vw) / life,
      x: GUST.startVw * this.vw,
      y: (GUST.yMinRatio + Math.random() * (GUST.yMaxRatio - GUST.yMinRatio)) * this.vh,
      alpha: GUST.opacityMin + Math.random() * (GUST.opacityMax - GUST.opacityMin),
      angle: GUST.angle,
    };
  }

  resetGust(g) {
    const life = GUST.lifeMin + Math.random() * (GUST.lifeMax - GUST.lifeMin);
    g.life = life;
    g.age = 0;
    g.length = (GUST.lengthVwMin + Math.random() * (GUST.lengthVwMax - GUST.lengthVwMin)) * this.vw;
    g.speed = (GUST.travelVw * this.vw) / life;
    g.x = GUST.startVw * this.vw;
    g.y = (GUST.yMinRatio + Math.random() * (GUST.yMaxRatio - GUST.yMinRatio)) * this.vh;
    g.alpha = GUST.opacityMin + Math.random() * (GUST.opacityMax - GUST.opacityMin);
  }

  renderGust(ctx, g) {
    if (g.y >= this.horizon) return;
    const t = g.age / g.life;
    let alpha;
    if (t < 0.18) alpha = g.alpha * (t / 0.18);
    else if (t <= 0.78) alpha = g.alpha;
    else alpha = g.alpha * (1 - (t - 0.78) / 0.22);
    alpha *= 1 + this.wind.gust * 0.3;
    if (alpha <= 0.002) return;

    const len = g.length * (0.65 + 0.55 * t);
    const angle = g.angle + (Math.atan2(this.wind.y, this.wind.x) * 180) / Math.PI * 0.2;
    drawGust(ctx, this.gustSprite, g.x, g.y, len, alpha, angle);
  }

  makeParticle() {
    const z = Math.random();
    const travel = TRAVEL_RATIO * this.vh;
    const life = snowDuration(z);
    const fallSpeed = travel / life;
    const age = Math.random() * life;
    const p = {
      type: "snow",
      z,
      x: Math.random() * this.vw,
      y: -0.06 * this.vh + (age / life) * travel,
      vx: 0,
      vy: 0,
      age,
      life,
      travel,
      fallSpeed,
      size: snowSize(z),
      alpha: alphaFor(z),
      response: SNOW.responseMin + Math.random() * (SNOW.responseMax - SNOW.responseMin),
      swayAmp: SNOW.swayAmpMin + Math.random() * (SNOW.swayAmpMax - SNOW.swayAmpMin),
      swayPeriod: 0.48 * life,
      swayPhase: Math.random() * Math.PI * 2,
    };
    p.vx = baseDriftFor(z);
    p.vy = fallSpeed;
    return p;
  }

  makeDust() {
    const z = Math.random();
    const period = DUST.periodMin + Math.random() * (DUST.periodMax - DUST.periodMin);
    return {
      type: "dust",
      z,
      x: Math.random() * this.vw,
      y: Math.random() * Math.max(1, this.horizon),
      vx: 0,
      vy: 0,
      size: segment(z, DUST.sizeFar, DUST.sizeMid, DUST.sizeNear),
      response: DUST.responseMin + Math.random() * (DUST.responseMax - DUST.responseMin),
      sinkSpeed: Math.random() * DUST.sinkMax,
      driftAmpX: DUST.driftVw * this.vw,
      driftAmpY: DUST.driftVh * this.vh,
      driftFullPeriod: period,
      driftPhase: Math.random() * Math.PI * 2,
      twinklePeriod: DUST.twinkleMin + Math.random() * (DUST.twinkleMax - DUST.twinkleMin),
      twinklePhase: Math.random() * Math.PI * 2,
      colorIndex: dustColorIndex(),
    };
  }

  recycle(p, wind) {
    const travel = TRAVEL_RATIO * this.vh;
    p.life = snowDuration(p.z);
    p.travel = travel;
    p.fallSpeed = travel / p.life;
    p.age = 0;
    p.y = -0.06 * this.vh;
    p.x = Math.random() * this.vw;
    p.swayPeriod = 0.48 * p.life;
    p.swayPhase = Math.random() * Math.PI * 2;
    p.vx = baseDriftFor(p.z) + wind.strength * wind.x * p.response;
    p.vy = p.fallSpeed + wind.y * wind.strength * p.response * 0.5;
  }

  currentWind() {
    if (!this.options.wind) return { x: 1, y: 0, strength: 0, gust: 0 };
    return sampleWind(this.simTime, this.gustState, this.options.gust);
  }

  step(dt) {
    this.simTime += dt;
    advanceGustState(this.gustState, this.simTime);
    const wind = this.currentWind();
    this.wind = wind;
    const snowA = 1 - Math.exp(-dt / SNOW.inertia);
    const dustA = 1 - Math.exp(-dt / DUST.inertia);

    for (const p of this.particles) {
      if (p.type === "dust") this.stepDust(p, dt, wind, dustA);
      else this.stepSnow(p, dt, wind, snowA);
    }
    for (const g of this.gusts) {
      g.age += dt;
      if (g.age >= g.life) this.resetGust(g);
      g.x += g.speed * dt;
    }
  }

  stepSnow(p, dt, wind, a) {
    p.age += dt;
    if (p.age >= p.life) {
      this.recycle(p, wind);
      return;
    }

    const targetVx = baseDriftFor(p.z) + wind.strength * wind.x * p.response;
    const targetVy = p.fallSpeed + wind.y * wind.strength * p.response * 0.5;
    p.vx += (targetVx - p.vx) * a;
    p.vy += (targetVy - p.vy) * a;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.swayPhase += (2 * Math.PI / p.swayPeriod) * dt;

    if (p.x < -60) p.x += this.vw + 120;
    else if (p.x > this.vw + 60) p.x -= this.vw + 120;
  }

  stepDust(p, dt, wind, a) {
    // 旧版 dust-drift：X/Y 同相位，沿一条斜线往返
    const rate = (2 * Math.PI) / p.driftFullPeriod;
    const c = Math.cos(p.driftPhase);
    const driftVx = p.driftAmpX * rate * c;
    const driftVy = p.driftAmpY * rate * c;
    p.driftPhase += rate * dt;

    const targetVx = driftVx + wind.x * wind.strength * p.response;
    const targetVy = driftVy + p.sinkSpeed + wind.y * wind.strength * p.response * 0.35;
    p.vx += (targetVx - p.vx) * a;
    p.vy += (targetVy - p.vy) * a;
    p.x += p.vx * dt;
    p.y += p.vy * dt;
    p.twinklePhase += (2 * Math.PI / p.twinklePeriod) * dt;

    // 横向换边；纵向越过天空上下界时重投
    if (p.x < -80) p.x += this.vw + 160;
    else if (p.x > this.vw + 80) p.x -= this.vw + 160;
    if (p.y >= this.horizon) {
      p.y = 0;
      p.x = Math.random() * this.vw;
    } else if (p.y < -20) {
      p.y = this.horizon - 10;
      p.x = Math.random() * this.vw;
    }
  }

  render() {
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.vw, this.vh);
    ctx.globalCompositeOperation = "source-over";

    for (const g of this.gusts) this.renderGust(ctx, g);
    for (const p of this.particles) {
      if (p.type === "dust") this.renderDust(ctx, p);
      else this.renderSnow(ctx, p);
    }
    ctx.globalAlpha = 1;
  }

  renderSnow(ctx, p) {
    const fadeIn = SNOW.fadeInRatio * p.life;
    const fadeOut = SNOW.fadeOutRatio * p.life;
    let fade = Math.min(1, p.age / fadeIn, (p.life - p.age) / fadeOut);
    fade = Math.max(0, Math.min(1, fade));
    if (fade <= 0) return;

    let horizonFade = 1;
    if (p.y > this.horizon) {
      horizonFade = Math.max(0, 1 - (p.y - this.horizon) / HORIZON_FADE_PX);
    }
    const alpha = p.alpha * fade * horizonFade;
    if (alpha <= 0.002) return;

    const drawX = p.x + p.swayAmp * Math.sin(p.swayPhase);
    drawSnowSprite(ctx, this.sprites, p.z, drawX, p.y, p.size, alpha);
  }

  renderDust(ctx, p) {
    // 只画天空区域；亮度和尺寸一起呼吸，对应旧版 dust-twinkle 的 opacity + scale
    if (p.y < 0 || p.y >= this.horizon) return;
    const t = 0.5 + 0.5 * Math.sin(p.twinklePhase);
    const alpha = DUST.alphaMin + (DUST.alphaMax - DUST.alphaMin) * t;
    const scale = DUST.scaleMin + (DUST.scaleMax - DUST.scaleMin) * t;
    drawDustSprite(ctx, this.dustSprites, p.z, p.colorIndex, p.x, p.y, p.size * scale, alpha);
  }

  frame(now) {
    const dt = Math.min((now - this.lastTime) / 1000, MAX_DELTA);
    this.lastTime = now;
    this.acc = Math.min(this.acc + dt, MAX_DELTA);

    let steps = 0;
    while (this.acc >= STEP && steps < MAX_STEPS) {
      this.step(STEP);
      this.acc -= STEP;
      steps += 1;
    }
    if (steps === MAX_STEPS) this.acc = this.acc % STEP;

    this.render();
    if (this.running) this.raf = requestAnimationFrame((t) => this.frame(t));
  }
}
