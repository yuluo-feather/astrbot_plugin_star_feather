// 预渲染雪片贴图：4 档模糊，运行时只 drawImage，不调用 ctx.filter。

const SPRITE_PX = 32;
const BLUR_STEPS = [0, 0.8, 1.8, 3];

let cache = null;

function makeSprite(blur) {
  const pad = Math.ceil(blur * 3);
  const size = SPRITE_PX + pad * 2;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const g = canvas.getContext("2d");
  const c = size / 2;
  const r = SPRITE_PX / 2;
  // 与旧版 .snowflake 的 radial-gradient 同一套色标
  const grad = g.createRadialGradient(c, c, 0, c, c, r);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.42, "rgba(226,242,255,0.9)");
  grad.addColorStop(0.74, "rgba(190,220,255,0.35)");
  grad.addColorStop(1, "rgba(190,220,255,0)");
  g.fillStyle = grad;
  if (blur > 0) g.filter = `blur(${blur}px)`;
  g.beginPath();
  g.arc(c, c, r, 0, Math.PI * 2);
  g.fill();
  return { canvas, size, base: SPRITE_PX };
}

export function ensureSnowSprites() {
  if (cache) return cache;
  cache = BLUR_STEPS.map(makeSprite);
  return cache;
}

// 依次是 [中心, 中间, 边缘] 三段色标，与旧版 .dust-mote::before 的 radial-gradient 对应
export const DUST_PALETTES = [
  [[255, 255, 255, 0.95], [206, 228, 255, 0.55], [180, 210, 255, 0]],
  [[255, 244, 214, 0.95], [240, 210, 150, 0.5], [240, 200, 130, 0]],
  [[214, 246, 255, 0.95], [150, 220, 245, 0.5], [120, 200, 235, 0]],
];

let dustCache = null;

function rgba(parts) {
  return `rgba(${parts[0]},${parts[1]},${parts[2]},${parts[3]})`;
}

function makeDustSprite(palette, blur) {
  const pad = Math.ceil(blur * 3);
  const size = SPRITE_PX + pad * 2;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const g = canvas.getContext("2d");
  const c = size / 2;
  const r = SPRITE_PX / 2;
  const grad = g.createRadialGradient(c, c, 0, c, c, r);
  grad.addColorStop(0, rgba(palette[0]));
  grad.addColorStop(0.42, rgba(palette[1]));
  grad.addColorStop(0.72, rgba(palette[2]));
  grad.addColorStop(1, "rgba(0,0,0,0)");
  g.fillStyle = grad;
  if (blur > 0) g.filter = `blur(${blur}px)`;
  g.beginPath();
  g.arc(c, c, r, 0, Math.PI * 2);
  g.fill();
  return { canvas, size, base: SPRITE_PX };
}

export function ensureDustSprites() {
  if (dustCache) return dustCache;
  dustCache = DUST_PALETTES.map((palette) => BLUR_STEPS.map((blur) => makeDustSprite(palette, blur)));
  return dustCache;
}

// sprites: [colorIndex][blurIndex]
export function drawDustSprite(ctx, sprites, z, colorIndex, x, y, size, alpha) {
  const group = sprites[colorIndex] || sprites[0];
  const pos = (1 - z) * (group.length - 1);
  const i0 = Math.floor(pos);
  const i1 = Math.min(group.length - 1, i0 + 1);
  const t = pos - i0;

  const draw = (sprite, a) => {
    if (a <= 0.002) return;
    const dst = size * (sprite.size / sprite.base);
    ctx.globalAlpha = a;
    ctx.drawImage(sprite.canvas, x - dst / 2, y - dst / 2, dst, dst);
  };

  draw(group[i0], alpha * (1 - t));
  if (i1 !== i0 && t > 0) draw(group[i1], alpha * t);
}

let gustCache = null;

// 横向渐变带：中间亮、两端透明，与旧版 .gust 的 linear-gradient + blur(3px) 一致
export function ensureGustSprite() {
  if (gustCache) return gustCache;
  const w = 512;
  const line = 3;
  const pad = 6;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = line + pad * 2;
  const g = canvas.getContext("2d");
  const grad = g.createLinearGradient(0, 0, w, 0);
  grad.addColorStop(0, "rgba(198,224,255,0)");
  grad.addColorStop(0.48, "rgba(222,240,255,0.7)");
  grad.addColorStop(1, "rgba(198,224,255,0)");
  g.fillStyle = grad;
  g.filter = "blur(3px)";
  g.fillRect(0, pad, w, line);
  gustCache = { canvas, h: line + pad * 2 };
  return gustCache;
}

export function drawGust(ctx, sprite, x, y, length, alpha, angleDeg) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate((angleDeg * Math.PI) / 180);
  ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
  ctx.drawImage(sprite.canvas, -length / 2, -sprite.h / 2, length, sprite.h);
  ctx.restore();
}

// z = 1 近景用最清晰档，z = 0 远景用最模糊档；相邻两档按透明度加权，避免跨档跳变。
export function drawSnowSprite(ctx, sprites, z, x, y, size, alpha) {
  const pos = (1 - z) * (sprites.length - 1);  const i0 = Math.floor(pos);
  const i1 = Math.min(sprites.length - 1, i0 + 1);
  const t = pos - i0;

  const draw = (sprite, a) => {
    if (a <= 0.002) return;
    // sprite 含模糊 padding；按 base 比例放大目标尺寸，保证视觉直径等于 size
    const dst = size * (sprite.size / sprite.base);
    ctx.globalAlpha = a;
    ctx.drawImage(sprite.canvas, x - dst / 2, y - dst / 2, dst, dst);
  };

  draw(sprites[i0], alpha * (1 - t));
  if (i1 !== i0 && t > 0) draw(sprites[i1], alpha * t);
}
