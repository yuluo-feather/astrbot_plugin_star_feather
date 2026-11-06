"""本地 smoke：预览页异步状态与减少动态效果。

运行：
    python tests/frontend/preview_async_smoke.py [view_dir]

三个场景（假 bridge 注入，不依赖 AstrBot）：
- drawStale：旧抽牌响应晚归，不能覆盖刷新后重新抽出的结果。
- aiStale：AI 解读在途时刷新并重新抽牌，旧解读不能落到新结果的界面上。
- reduced：prefers-reduced-motion 下翻牌立即可见，不等 animationend。
需要本机安装 Playwright 与 Edge；缺少依赖时返回非零，不静默通过。
"""
import functools
import http.server
import sys
import threading
from pathlib import Path

DEFAULT_VIEW_DIR = Path(__file__).resolve().parents[2] / "views" / "star-feather"

BRIDGE = r"""
(() => {
  const px = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
  const scenario = new URLSearchParams(location.search).get("scenario") || "drawStale";
  let drawCalls = 0;
  let aiCalls = 0;
  let releaseDraw = null;
  let releaseAi = null;
  const drawGate = new Promise((resolve) => { releaseDraw = resolve; });
  const aiGate = new Promise((resolve) => { releaseAi = resolve; });
  window.__releaseDraw = () => { if (releaseDraw) releaseDraw(); };
  window.__releaseAi = () => { if (releaseAi) releaseAi(); };
  const pick = (id, cn) => ({
    id, cn, en: "", suit: "major", num: "0", upright: true,
    keywords: ["关键词"], what: "牌义", advice: "建议", energy: "",
  });
  const draw = (which) => ({
    spread: "羽时三刻", question: "",
    positions: ["过去", "现在", "未来"],
    picks: which === "old"
      ? [pick("major_00", "愚者"), pick("major_00", "愚者"), pick("major_00", "愚者")]
      : [pick("major_01", "魔术师"), pick("major_01", "魔术师"), pick("major_01", "魔术师")],
  });
  const ai = (text) => ({ text, sections: [text], summary: "" });
  window.AstrBotPluginView = {
    ready: async () => {},
    t: (_key, fallback) => fallback,
    apiGet: async (endpoint) => {
      if (endpoint === "overview") {
        return {
          version: "0.10.0", date: "20261107", users: 0, with_spirit: 0,
          stale: 0, topics: 0,
          settings: { enable_ai: true, spirit_line: true, structure_check: "loose",
                      persona: "random", send_mode: "forward", daily_card: true },
        };
      }
      if (endpoint === "daily/list") {
        return { date: "20261107", indexed_total: 0, shown: 0, truncated: false, items: [] };
      }
      if (endpoint === "spreads") {
        return { items: [{ name: "羽时三刻", scenario: "时间线",
                           positions: [{ name: "过去", meaning: "" },
                                       { name: "现在", meaning: "" },
                                       { name: "未来", meaning: "" }] }] };
      }
      if (endpoint === "card-image") return { mime: "image/png", base64: px };
      throw new Error("unexpected endpoint: " + endpoint);
    },
    apiPost: async (endpoint) => {
      if (endpoint === "preview/draw") {
        drawCalls += 1;
        window.__drawCalls = drawCalls;
        if (scenario === "drawStale" && drawCalls === 1) {
          await drawGate;
          return draw("old");
        }
        return draw("new");
      }
      if (endpoint === "preview/interpret") {
        aiCalls += 1;
        window.__aiCalls = aiCalls;
        if (scenario === "aiStale") {
          await aiGate;
          return ai("旧解读");
        }
        return ai("新解读");
      }
      throw new Error("unexpected endpoint: " + endpoint);
    },
  };
})();
"""

CARD_TITLES = """Array.from(document.querySelectorAll("#preview-result .pick-title strong"))
  .map((node) => node.textContent)"""
FLIPPED_ALL = """(() => {
  const cards = document.querySelectorAll("#preview-result .pick-card");
  return cards.length > 0
    && document.querySelectorAll("#preview-result .pick-card.card-flipped").length === cards.length;
})()"""


def open_preview(page):
    page.click('[data-tab="preview"]')
    page.wait_for_selector("#preview-spread option", state="attached", timeout=5000)
    page.wait_for_function("!document.querySelector('#preview-draw').disabled", timeout=5000)


def draw_once(page):
    page.click("#preview-draw")
    page.wait_for_selector("#preview-result .pick-card", timeout=5000)


def run_scenario(browser, base_url, scenario):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    try:
        if scenario == "reduced":
            page.emulate_media(reduced_motion="reduce")
        page.add_init_script(BRIDGE)
        page.goto(f"{base_url}/index.html?scenario={scenario}", wait_until="networkidle")
        open_preview(page)
        if scenario == "drawStale":
            page.click("#preview-draw")
            page.wait_for_function("window.__drawCalls === 1", timeout=5000)
            page.click("#refresh")  # 刷新会清空在途抽牌
            page.wait_for_function("!document.querySelector('#preview-draw').disabled", timeout=5000)
            draw_once(page)
            page.wait_for_function(
                "document.querySelector('#preview-result .pick-title strong')?.textContent === '魔术师'",
                timeout=5000)
            page.evaluate("window.__releaseDraw()")
            page.wait_for_timeout(300)
            return {"scenario": scenario,
                    "titles": page.evaluate(CARD_TITLES)}
        if scenario == "aiStale":
            draw_once(page)
            page.wait_for_function("!document.querySelector('#preview-ai').disabled", timeout=5000)
            page.click("#preview-ai")
            page.wait_for_function("window.__aiCalls === 1", timeout=5000)
            page.click("#refresh")
            page.wait_for_function("!document.querySelector('#preview-draw').disabled", timeout=5000)
            # 刷新后重新抽牌，让旧 AI 落在一个已经换了内容的结果上
            draw_once(page)
            page.evaluate("window.__releaseAi()")
            page.wait_for_timeout(300)
            return {"scenario": scenario,
                    "text": page.inner_text("#preview-result"),
                    "titles": page.evaluate(CARD_TITLES)}
        draw_once(page)
        page.wait_for_function(FLIPPED_ALL, timeout=300)
        return {"scenario": scenario, "flipped": True}
    except Exception as error:  # 超时也算失败，交给 check() 判定
        return {"scenario": scenario, "error": type(error).__name__}
    finally:
        page.close()


def check(result):
    if result.get("error"):
        return False
    if result["scenario"] == "drawStale":
        return set(result["titles"]) == {"魔术师"}
    if result["scenario"] == "aiStale":
        # 旧 AI 不能改写新结果的 UI；新结果仍是刚抽出的那张牌
        text = result["text"]
        return (set(result["titles"]) == {"魔术师"}
                and "旧解读" not in text and "点上方" in text)
    return result.get("flipped") is True


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
    base_url = f"http://127.0.0.1:{port}"
    results = []
    try:
        with sync_playwright() as p:
            browser = launch(p)
            for scenario in ("drawStale", "aiStale", "reduced"):
                results.append(run_scenario(browser, base_url, scenario))
            browser.close()
    finally:
        server.shutdown()
    print(results)
    ok = all(check(item) for item in results)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
