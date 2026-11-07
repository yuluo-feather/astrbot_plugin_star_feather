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

// z = 1 近景用最清晰档，z = 0 远景用最模糊档；相邻两档按透明度加权，避免跨档跳变。
export function drawSnowSprite(ctx, sprites, z, x, y, size, alpha) {
  const pos = (1 - z) * (sprites.length - 1);
  const i0 = Math.floor(pos);
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
