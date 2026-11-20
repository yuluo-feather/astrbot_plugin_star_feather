"""本地 smoke：插件页与宿主 bridge 的连接契约。

运行：
    python tests/frontend/host_contract_smoke.py

不依赖 AstrBot：在页面加载前注入假 bridge，覆盖四种场景：
- ready：正常握手，数据可见；
- timeout：bridge 存在但 ready 永不返回，3 秒后进入 degraded（空数据 + 错误条）；
- late：ready 在超时之后才返回，页面应从 degraded 自动恢复到 connected；
- light：宿主 data-theme=light，页面仍保持深色（color-scheme: dark）。

需要本机安装 Playwright 与 Edge；缺少依赖时返回 2，不静默通过。
"""
import functools
import http.server
import sys
import threading
from pathlib import Path

DEFAULT_VIEW_DIR = Path(__file__).resolve().parents[2] / "views" / "star-feather"

FAKE_BRIDGE = r"""
(() => {
  const scenario = new URLSearchParams(location.search).get("host") || "ready";
  let resolved = false;
  let resolveReady = null;
  const ready = new Promise((resolve) => { resolveReady = resolve; });
  const markReady = () => { resolved = true; resolveReady(); };

  if (scenario === "ready" || scenario === "light") setTimeout(markReady, 50);
  if (scenario === "late") setTimeout(markReady, 4200);
  if (scenario === "light") {
    const applyTheme = () => {
      if (document.documentElement) {
        document.documentElement.setAttribute("data-theme", "light");
      }
    };
    if (document.documentElement) applyTheme();
    else document.addEventListener("DOMContentLoaded", applyTheme, { once: true });
  }

  const records = {
    "overview": {
      version: "9.9.9", date: "20261121", users: 1, with_spirit: 0,
      stale: 0, topics: 1,
      settings: { enable_ai: false, spirit_line: false, structure_check: "loose",
                  persona: "off", send_mode: "plain", daily_card: true },
    },
    "daily/list": {
      date: "20261121",
      items: [{
        uid: "test:1", card_id: "major_00", card_cn: "愚者", upright: true,
        spirit: "", topics: 1, stale: false,
      }],
      indexed_total: 1, shown: 1, truncated: false,
    },
    "daily/detail": {
      uid: "test:1", date: "20261121",
      card: { id: "major_00", cn: "愚者", suit: "major", num: "0" },
      upright: true, stale: false, persona: "off", spirit: "",
      topics: [{ topic: "测试主题", text: "测试解读" }],
    },
    "cards": { items: [{ id: "major_00", cn: "愚者", en: "The Fool", suit: "major" }] },
    "spreads": {
      items: [{
        name: "羽签", scenario: "测试牌阵",
        positions: [{ name: "当下", meaning: "此刻" }],
      }],
    },
  };

  window.AstrBotPluginView = {
    ready: () => ready,
    t: (_key, fallback) => fallback,
    apiGet: async (endpoint) => {
      if (!resolved) throw new Error("bridge not ready");
      if (!(endpoint in records)) throw new Error("unknown endpoint " + endpoint);
      return records[endpoint];
    },
    apiPost: async () => ({}),
  };
})();
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
        print("SKIP: view dir not found: " + str(view_dir))
        return 2

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(view_dir))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(port)
    failures = []

    def check(name, condition, detail=""):
        if not condition:
            failures.append("{}: {}".format(name, detail))

    try:
        with sync_playwright() as p:
            browser = launch(p)

            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.add_init_script(FAKE_BRIDGE)
            page.goto(base + "/?host=ready", wait_until="load")
            page.wait_for_selector(".user", timeout=5000)
            check("ready.banner", page.locator("#connection-banner").is_hidden())
            check("ready.user", page.locator(".user").count() == 1)
            check("ready.stats", "1" in page.locator("#stats").inner_text())
            check(
                "ready.title",
                page.locator("#heading").inner_text() == "星羽塔罗 · 观测台",
            )
            check(
                "ready.canvas",
                page.evaluate("document.querySelector('#environment-canvas').width > 0"),
            )
            page.close()

            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.add_init_script(FAKE_BRIDGE)
            page.goto(base + "/?host=timeout", wait_until="load")
            page.wait_for_timeout(3400)
            check(
                "timeout.banner",
                page.locator("#connection-banner").is_visible(),
            )
            check(
                "timeout.message",
                "超时" in page.locator("#connection-text").inner_text(),
            )
            check(
                "timeout.no_demo",
                "webchat:10001" not in page.content(),
            )
            check(
                "timeout.empty",
                "连接 AstrBot 后显示今日牌运"
                in page.locator("#daily-list").inner_text(),
            )
            check(
                "timeout.canvas",
                page.evaluate("document.querySelector('#environment-canvas').width > 0"),
            )
            check(
                "timeout.retry",
                page.locator("#connection-retry").is_enabled(),
            )
            page.close()

            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.add_init_script(FAKE_BRIDGE)
            page.goto(base + "/?host=late", wait_until="load")
            page.wait_for_timeout(3400)
            check(
                "late.degraded_first",
                page.locator("#connection-banner").is_visible(),
            )
            page.wait_for_function(
                "document.querySelector('#connection-banner')"
                ".classList.contains('hidden')",
                timeout=5000,
            )
            page.wait_for_selector(".user", timeout=5000)
            check(
                "late.recovered",
                page.locator("#connection-banner").is_hidden()
                and page.locator(".user").count() == 1,
            )
            page.close()

            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.add_init_script(FAKE_BRIDGE)
            page.goto(base + "/?host=light", wait_until="load")
            page.wait_for_timeout(1000)
            check(
                "light.data_theme",
                page.evaluate(
                    "document.documentElement.getAttribute('data-theme')") == "light",
            )
            check(
                "light.color_scheme",
                page.evaluate(
                    "getComputedStyle(document.documentElement).colorScheme")
                == "dark",
            )
            check(
                "light.background",
                page.evaluate(
                    "getComputedStyle(document.documentElement).backgroundColor")
                != "rgb(255, 255, 255)",
            )
            check("light.banner", page.locator("#connection-banner").is_hidden())
            page.close()

            browser.close()
    finally:
        server.shutdown()

    if failures:
        for failure in failures:
            print("FAIL: " + failure)
        print("FAIL")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
