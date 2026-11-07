"""本地 smoke：背景隔离（UI 切换 / 弹窗 / 抽牌不改变背景层）。

运行：
    python tests/frontend/background_isolation_smoke.py [view_dir]

不依赖 AstrBot：只加载 views/ 静态页，检查：
- 背景 canvas 的尺寸与位置在 UI 操作前后不变；
- `.sky` 仍是 `position: fixed`、`z-index: -1`、`pointer-events: none`；
- 环境 canvas 仍在绘制（非透明像素比例 > 0）；
- 弹窗遮罩 z-index 为 60。

需要本机安装 Playwright 与 Edge；缺少依赖时返回 2，不静默通过。
"""
import functools
import http.server
import sys
import threading
from pathlib import Path

DEFAULT_VIEW_DIR = Path(__file__).resolve().parents[2] / "views" / "star-feather"

CHECK = """
() => {
  const env = document.querySelector('#environment-canvas');
  const meteor = document.querySelector('#meteor-canvas');
  const water = document.querySelector('#meteor-water');
  const sky = document.querySelector('.sky');
  const modal = document.querySelector('#card-modal');
  const ctx = env.getContext('2d');
  const data = ctx.getImageData(0, 0, env.width, env.height).data;
  const step = 4 * 97;
  let opaque = 0;
  let sampled = 0;
  for (let i = 3; i < data.length; i += step) {
    sampled += 1;
    if (data[i] > 0) opaque += 1;
  }
  return {
    env: [env.width, env.height],
    meteor: [meteor.width, meteor.height],
    water: [water.width, water.height],
    skyRect: [Math.round(sky.getBoundingClientRect().width),
              Math.round(sky.getBoundingClientRect().height)],
    skyZ: getComputedStyle(sky).zIndex,
    skyPE: getComputedStyle(sky).pointerEvents,
    skyPos: getComputedStyle(sky).position,
    particleRatio: sampled ? opaque / sampled : 0,
    modalZ: modal ? getComputedStyle(modal).zIndex : '',
  };
}
"""


def main():
    view_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_VIEW_DIR
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed")
        return 2
    from _browser import launch
    if not view_dir.is_dir():
        print(f"SKIP: view dir not found: {view_dir}")
        return 2

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(view_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    samples = {}
    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.goto(url, wait_until="load")
            page.wait_for_timeout(1000)
            samples["baseline"] = page.evaluate(CHECK)

            page.click('.tab[data-tab="cards"]')
            page.wait_for_selector(".card-cell", timeout=5000)
            page.wait_for_timeout(300)
            samples["cards"] = page.evaluate(CHECK)

            page.click(".card-cell")
            page.wait_for_selector("#card-modal:not(.hidden)", timeout=3000)
            page.wait_for_timeout(300)
            samples["modal"] = page.evaluate(CHECK)

            page.click("#card-modal-close")
            page.click('.tab[data-tab="preview"]')
            page.wait_for_function(
                "!document.querySelector('#preview-draw').disabled", timeout=5000)
            page.click("#preview-draw")
            page.wait_for_selector(".pick-card.card-flipped", timeout=5000)
            page.wait_for_timeout(500)
            samples["preview"] = page.evaluate(CHECK)
            browser.close()
    finally:
        server.shutdown()

    ok = True
    base = samples["baseline"]
    for name, sample in samples.items():
        for key in ("env", "meteor", "water", "skyRect"):
            if sample[key] != base[key]:
                ok = False
                print(f"FAIL: {name}.{key} changed {base[key]} -> {sample[key]}")
        if sample["skyZ"] != "-1" or sample["skyPE"] != "none" or sample["skyPos"] != "fixed":
            ok = False
            print(f"FAIL: {name} sky layer "
                  f"{sample['skyZ']}/{sample['skyPE']}/{sample['skyPos']}")
        if sample["particleRatio"] <= 0:
            ok = False
            print(f"FAIL: {name} environment canvas not painting")
    if samples["modal"]["modalZ"] != "60":
        ok = False
        print(f"FAIL: modal z-index {samples['modal']['modalZ']}")
    print(samples)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
