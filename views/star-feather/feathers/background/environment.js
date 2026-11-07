// 环境粒子系统（PR1：只实现雪）。
// 风场状态、模拟时间与粒子都在这里；全局监听由 background/index.js 转交。
import { advanceGustState, createGustState, sampleWind } from "./wind-field.js";
import { drawSnowSprite, ensureSnowSprites } from "./environment-sprites.js";

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
    this.sprites = ensureSnowSprites();
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
        // life（即旧版动画时长）不随视口变化；速度随 110vh 重算
        p.travel = TRAVEL_RATIO * this.vh;
        p.fallSpeed = p.travel / p.life;
        p.swayPeriod = 0.48 * p.life;
      }
    } else {
      this.spawnAll();
    }
    this.render();
  }

  spawnAll() {
    this.particles = [];
    for (let i = 0; i < SNOW.count; i += 1) this.particles.push(this.makeParticle());
    // 远处的先画，近处的后画
    this.particles.sort((a, b) => a.z - b.z);
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
    const a = 1 - Math.exp(-dt / SNOW.inertia);

    for (const p of this.particles) {
      p.age += dt;
      if (p.age >= p.life) {
        this.recycle(p, wind);
        continue;
      }

      const targetVx = baseDriftFor(p.z) + wind.strength * wind.x * p.response;
      const targetVy = p.fallSpeed + wind.y * wind.strength * p.response * 0.5;
      p.vx += (targetVx - p.vx) * a;
      p.vy += (targetVy - p.vy) * a;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.swayPhase += (2 * Math.PI / p.swayPeriod) * dt;

      // 横向越界换边
      if (p.x < -60) p.x += this.vw + 120;
      else if (p.x > this.vw + 60) p.x -= this.vw + 120;
    }
  }

  render() {
    const ctx = this.ctx;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.vw, this.vh);
    ctx.globalCompositeOperation = "source-over";

    for (const p of this.particles) {
      const fadeIn = SNOW.fadeInRatio * p.life;
      const fadeOut = SNOW.fadeOutRatio * p.life;
      let fade = Math.min(1, p.age / fadeIn, (p.life - p.age) / fadeOut);
      fade = Math.max(0, Math.min(1, fade));
      if (fade <= 0) continue;

      let horizonFade = 1;
      if (p.y > this.horizon) {
        horizonFade = Math.max(0, 1 - (p.y - this.horizon) / HORIZON_FADE_PX);
      }
      const alpha = p.alpha * fade * horizonFade;
      if (alpha <= 0.002) continue;

      const drawX = p.x + p.swayAmp * Math.sin(p.swayPhase);
      drawSnowSprite(ctx, this.sprites, p.z, drawX, p.y, p.size, alpha);
    }
    ctx.globalAlpha = 1;
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
