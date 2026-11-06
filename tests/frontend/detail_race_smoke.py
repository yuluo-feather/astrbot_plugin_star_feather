"""本地 smoke：同一 UID 的详情请求竞态。

运行：
    python tests/frontend/detail_race_smoke.py

不依赖 AstrBot：在页面加载前注入假的 AstrBotPluginView，用可控延迟返回
两次 daily/detail，验证旧请求（成功或失败）晚返回时不会覆盖新详情。
需要本机安装 Playwright 与 Edge；未安装时脚本会明确跳过。
"""
import functools
import http.server
import sys
import threading
from pathlib import Path

VIEW_DIR = Path(__file__).resolve().parents[2] / "views" / "star-feather"

BRIDGE = r"""
(() => {
  const px = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const scenario = new URLSearchParams(location.search).get("race") || "success";
  let detailCalls = 0;
  const card = (id, cn, num) => ({ id, cn, suit: "major", num });
  const detail = (which, delay, fail) => ({
    uid: "u1", date: "20261107", today: "20261107",
    card: which === "old" ? card("major_00", "愚者", "0")
      : which === "new" ? card("major_01", "魔术师", "1")
      : card("major_02", "女祭司", "2"),
    upright: true, spirit: "", sign: "", persona: "", stale: false,
    topics: [{ topic: "测试", text: "测试解读", sections: [], summary: "" }],
    _delay: delay, _fail: fail,
  });
  const send = async (payload) => {
    await wait(payload._delay || 0);
    if (payload._fail) throw new Error("旧请求失败");
    const { _delay, _fail, ...data } = payload;
    return data;
  };
  window.AstrBotPluginView = {
    ready: async () => {},
    t: (_key, fallback) => fallback,
    apiGet: async (endpoint) => {
      if (endpoint === "overview") {
        return {
          version: "0.10.0", date: "20261107", users: 1, with_spirit: 0,
          stale: 0, topics: 0,
          settings: { enable_ai: true, spirit_line: true, structure_check: "loose",
                      persona: "random", send_mode: "forward", daily_card: true },
        };
      }
      if (endpoint === "daily/list") {
        return {
          date: "20261107", indexed_total: 1, shown: 1, truncated: false,
          items: [{ uid: "u1", card_id: "major_02", card_cn: "女祭司",
                    upright: true, spirit: "", topics: 0, stale: false }],
        };
      }
      if (endpoint === "card-image") return { mime: "image/png", base64: px };
      if (endpoint === "daily/detail") {
        detailCalls += 1;
        if (detailCalls === 1) return send(detail("base", 0, false));
        if (detailCalls === 2) {
          return send(detail("old", 600, scenario === "failure"));
        }
        return send(detail("new", 0, false));
      }
      throw new Error("unexpected endpoint: " + endpoint);
    },
    apiPost: async () => { throw new Error("not used"); },
  };
})();
"""


def run_scenario(browser, base_url, scenario):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.add_init_script(BRIDGE)
    page.goto(f"{base_url}/index.html?race={scenario}", wait_until="networkidle")
    page.wait_for_selector(".detail-title h2", timeout=5000)
    # 第一次详情来自 boot；随后同一用户连点两次，制造同 UID 双请求。
    page.locator(".user").first.click()
    page.locator(".user").first.click()
    page.wait_for_timeout(900)
    title = page.locator(".detail-title h2").inner_text()
    error = page.locator("#daily-detail .empty").count()
    page.close()
    return {"scenario": scenario, "title": title, "errorBox": bool(error)}


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed")
        return 0
    if not VIEW_DIR.is_dir():
        print(f"SKIP: view dir not found: {VIEW_DIR}")
        return 0
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(VIEW_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{port}"
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            results.append(run_scenario(browser, base_url, "success"))
            results.append(run_scenario(browser, base_url, "failure"))
            browser.close()
    finally:
        server.shutdown()
    print(results)
    ok = all(item["title"] == "魔术师" and not item["errorBox"] for item in results)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
