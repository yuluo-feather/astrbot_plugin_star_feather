"""本地 smoke：缩略图进行中请求去重（OPT-05）。

运行：
    python tests/frontend/thumb_dedupe_smoke.py [view_dir]

不依赖 AstrBot：页面加载前注入假的 AstrBotPluginView，让多个用户共用同一张牌
（列表触发同 key 并发请求），并给 card-image 加延迟以制造重叠。
`retry` 场景让首次请求失败，刷新后必须重新发请求而不是复用 rejected Promise。
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
  const mode = new URLSearchParams(location.search).get("mode") || "dedupe";
  const calls = {};
  window.__thumbCalls = calls;
  const users = Array.from({ length: 6 }, (_, i) => ({
    uid: "u" + i, card_id: "major_00", card_cn: "愚者",
    upright: i % 2 === 0, spirit: "", topics: 0, stale: false,
  }));
  const detail = {
    uid: "u0", date: "20261107", today: "20261107",
    card: { id: "major_00", cn: "愚者", suit: "major", num: "0" },
    upright: true, spirit: "", sign: "", persona: "", stale: false,
    topics: [{ topic: "测试", text: "测试解读", sections: [], summary: "" }],
  };
  window.AstrBotPluginView = {
    ready: async () => {},
    t: (_key, fallback) => fallback,
    apiGet: async (endpoint, params = {}) => {
      if (endpoint === "overview") {
        return {
          version: "0.10.0", date: "20261107", users: 6, with_spirit: 0,
          stale: 0, topics: 0,
          settings: { enable_ai: true, spirit_line: true, structure_check: "loose",
                      persona: "random", send_mode: "forward", daily_card: true },
        };
      }
      if (endpoint === "daily/list") {
        return {
          date: "20261107", indexed_total: 6, shown: 6, truncated: false,
          items: users,
        };
      }
      if (endpoint === "daily/detail") return detail;
      if (endpoint === "card-image") {
        const key = `${params.id}|${params.upright}|${params.w}`;
        calls[key] = (calls[key] || 0) + 1;
        await new Promise((resolve) => setTimeout(resolve, 80));
        if (mode === "retry" && calls[key] === 1) throw new Error("模拟失败");
        return { mime: "image/png", base64: px };
      }
      throw new Error("unexpected endpoint: " + endpoint);
    },
    apiPost: async () => { throw new Error("not used"); },
  };
})();
"""

LIST_KEYS_READY = """(() => {
  const c = window.__thumbCalls;
  return c["major_00|1|84"] >= 1 && c["major_00|0|84"] >= 1;
})()"""
FAILED = """document.querySelectorAll(".user img.thumb.thumb-error").length >= 2"""
RETRIED = """(() => {
  const c = window.__thumbCalls;
  return c["major_00|1|84"] === 2 && c["major_00|0|84"] === 2;
})()"""
READY = """document.querySelectorAll(".user img.thumb[src^='data:']").length >= 2"""


def run_scenario(browser, base_url, mode):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    try:
        page.add_init_script(BRIDGE)
        page.goto(f"{base_url}/index.html?mode={mode}", wait_until="networkidle")
        page.wait_for_selector(".user img.thumb", timeout=5000)
        try:
            if mode == "retry":
                page.wait_for_function(FAILED, timeout=5000)
                page.click("#refresh")
                page.wait_for_function(RETRIED, timeout=5000)
                page.wait_for_function(READY, timeout=5000)
            else:
                page.wait_for_function(LIST_KEYS_READY, timeout=5000)
                # 再等一轮，确认没有第二波重复请求打进来
                page.wait_for_timeout(200)
        except Exception as error:  # 超时也是一种失败，交给 check() 判定
            return {"mode": mode, "calls": page.evaluate("window.__thumbCalls"),
                    "error": type(error).__name__}
        return {"mode": mode, "calls": page.evaluate("window.__thumbCalls")}
    finally:
        page.close()


def check(result):
    if result.get("error"):
        return False
    calls = result["calls"]
    if result["mode"] == "retry":
        # 首次失败后清理了进行中状态，刷新才能再发一次
        return calls.get("major_00|1|84") == 2 and calls.get("major_00|0|84") == 2
    return (calls.get("major_00|1|84") == 1
            and calls.get("major_00|0|84") == 1)


def main():
    view_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_VIEW_DIR
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed")
        return 2
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
            browser = p.chromium.launch(channel="msedge", headless=True)
            for mode in ("dedupe", "retry"):
                results.append(run_scenario(browser, base_url, mode))
            browser.close()
    finally:
        server.shutdown()
    print(results)
    ok = all(check(item) for item in results)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
