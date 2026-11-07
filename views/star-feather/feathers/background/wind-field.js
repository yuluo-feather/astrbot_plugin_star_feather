// 风场：阵风状态由 environment.js 持有，这里只提供状态创建、推进与纯采样。

const BASE_ANGLE = 0; // 0° = 向右
const DIR_WAVE_1 = { amp: 6, period: 18 };
const DIR_WAVE_2 = { amp: 2.5, period: 31 };
const BASE_SPEED = 10; // px/s
const SPEED_WAVE = { amp: 3, period: 15 };
const GUST_DURATION = { min: 2.5, max: 4 };
const GUST_GAP = { min: 8, max: 16 };
const GUST_HORIZON = 90; // 秒

function nextRandom(state) {
  // xorshift32；零状态由 createGustState 归一化
  let s = state.rngState;
  s ^= s << 13; s >>>= 0;
  s ^= s >>> 17;
  s ^= s << 5; s >>>= 0;
  state.rngState = s;
  return s / 0x100000000;
}

function smoothstep(x) {
  const t = Math.max(0, Math.min(1, x));
  return t * t * (3 - 2 * t);
}

export function createGustState(seed, peakMultiplier, horizonSeconds = GUST_HORIZON) {
  const state = {
    entries: [],
    rngState: (seed >>> 0) || 0x9e3779b9,
    nextStart: 0,
    horizon: horizonSeconds,
    peakMultiplier,
  };
  // 首次阵风也等待 8–16 s，消耗一次随机状态
  state.nextStart = 8 + nextRandom(state) * 8;
  return state;
}

export function advanceGustState(state, simTime) {
  state.entries = state.entries.filter((e) => e.start + e.duration > simTime);
  while (state.nextStart < simTime + state.horizon) {
    // 随机数消耗顺序固定：duration → peak → gap
    const duration = GUST_DURATION.min
      + nextRandom(state) * (GUST_DURATION.max - GUST_DURATION.min);
    const peak = 1 + nextRandom(state) * (state.peakMultiplier - 1);
    const gap = GUST_GAP.min + nextRandom(state) * (GUST_GAP.max - GUST_GAP.min);
    state.entries.push({ start: state.nextStart, duration, peak });
    state.nextStart += duration + gap;
  }
  return state;
}

export function sampleWind(simTime, state, useGust = true) {
  const angle = BASE_ANGLE
    + DIR_WAVE_1.amp * Math.sin((2 * Math.PI * simTime) / DIR_WAVE_1.period)
    + DIR_WAVE_2.amp * Math.sin((2 * Math.PI * simTime) / DIR_WAVE_2.period);
  const speed = BASE_SPEED
    + SPEED_WAVE.amp * Math.sin((2 * Math.PI * simTime) / SPEED_WAVE.period);

  let multiplier = 1;
  if (useGust) {
    for (const entry of state.entries) {
      if (simTime < entry.start || simTime > entry.start + entry.duration) continue;
      const p = (simTime - entry.start) / entry.duration;
      const curve = p < 0.5 ? smoothstep(p * 2) : smoothstep((1 - p) * 2);
      const m = 1 + (entry.peak - 1) * curve;
      if (m > multiplier) multiplier = m;
    }
  }

  const strength = Math.max(0, speed) * multiplier;
  const rad = (angle * Math.PI) / 180;
  const denom = state.peakMultiplier - 1;
  return {
    x: Math.cos(rad),
    y: Math.sin(rad),
    strength,
    gust: denom > 0 ? Math.max(0, Math.min(1, (multiplier - 1) / denom)) : 0,
  };
}

export const WIND_TUNING = {
  BASE_ANGLE, DIR_WAVE_1, DIR_WAVE_2, BASE_SPEED, SPEED_WAVE,
  GUST_DURATION, GUST_GAP, GUST_HORIZON,
};
