"""本地 smoke：数据五态与失败恢复。

运行：
    python tests/frontend/error_state_smoke.py

不依赖 AstrBot：注入假 bridge，覆盖：
- 同 uid 刷新失败保留旧详情，错误条可重试；
- 跨 uid 失败显示“该用户读取失败”，不保留上一个用户详情；
- cards 首次失败有加载态与错误条，重复进入只发一次请求，重试成功；
- 灯箱大图失败有内联提示与重试；
- 手动刷新有 aria-busy / 文案与结果 toast；
- 每日空态文案指向聊天抽签。

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
  const scenario = new URLSearchParams(location.search).get("errors") || "";
  const px = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
  const calls = { overview: 0, list: 0, detail: 0, cards: 0, image: 0, image420: 0 };
  window.__calls = calls;
  const card = (id, cn, num) => ({ id, cn, suit: "major", num });
  const item = (uid, cn) => ({
    uid, card_id: uid === "u1" ? "major_00" : "major_01", card_cn: cn,
    upright: true, spirit: "", topics: 1, stale: false,
  });
  const detail = (uid) => ({
    uid, date: "20261121",
    card: uid === "u1" ? card("major_00", "愚者", "0") : card("major_01", "魔术师", "1"),
    upright: true, stale: false, persona: "", spirit: "",
    topics: [{ topic: "测试", text: "测试解读" }],
  });
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const records = {
    "cards": { items: [{ id: "major_00", cn: "愚者", en: "The Fool", suit: "major" }] },
    "spreads": { items: [{ name: "羽签", scenario: "测试", positions: [{ name: "当下", meaning: "" }] }] },
    "card-detail": {
      id: "major_00", cn: "愚者", en: "The Fool", suit: "major", num: "0",
      up: { keywords: ["开始"], what: "测试", how: "测试" },
      down: { keywords: ["冲动"], what: "测试", turn: "测试", energy: "测试" },
    },
  };

  window.AstrBotPluginView = {
    ready: async () => {},
    t: (_key, fallback) => fallback,
    apiGet: async (endpoint, params = {}) => {
      if (endpoint === "overview") {
        calls.overview += 1;
        if (scenario === "slow") await wait(1200);
        return {
          version: "9.9.9", date: "20261121", users: 2, with_spirit: 0,
          stale: 0, topics: 2,
          settings: { enable_ai: false, spirit_line: false, structure_check: "loose",
                      persona: "off", send_mode: "plain", daily_card: true },
        };
      }
      if (endpoint === "daily/list") {
        calls.list += 1;
        const items = scenario === "empty" ? [] : [item("u1", "愚者"), item("u2", "魔术师")];
        return {
          date: "20261121", items,
          indexed_total: items.length, shown: items.length, truncated: false,
        };
      }
      if (endpoint === "daily/detail") {
        calls.detail += 1;
        if (scenario === "detail") {
          if (calls.detail === 2) throw new Error("刷新失败");
          if (calls.detail >= 3) {
            return { ...detail("u1"), card: card("major_01", "魔术师", "1") };
          }
        }
        if (scenario === "cross" && params.uid === "u2") throw new Error("读取失败");
        return detail(params.uid);
      }
      if (endpoint === "cards") {
        calls.cards += 1;
        if (scenario === "cards") {
          await wait(1200);
          if (calls.cards === 1) throw new Error("牌面接口失败");
        }
        return records.cards;
      }
      if (endpoint === "spreads") {
        if (scenario === "preview") await wait(1200);
        return records.spreads;
      }
      if (endpoint === "card-image") {
        calls.image += 1;
        if (params.w === 420) calls.image420 += 1;
        if (scenario === "decode" && params.w === 420) {
          return { mime: "image/png", base64: "AAAA" };
        }
        if (scenario === "lightbox" && params.w === 420) {
          await wait(400);
          if (calls.image420 === 1) throw new Error("大图失败");
        }
        return { mime: "image/png", base64: px };
      }
      if (endpoint in records) return records[endpoint];
      throw new Error("unknown endpoint " + endpoint);
    },
    apiPost: async () => { throw new Error("not used"); },
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

    def open_page(browser, scenario):
        page = browser.new_page(viewport={"width": 1180, "height": 900})
        page.add_init_script(FAKE_BRIDGE)
        page.goto(base + "/?errors=" + scenario, wait_until="load")
        return page

    try:
        with sync_playwright() as p:
            browser = launch(p)

            page = open_page(browser, "detail")
            page.wait_for_selector(".detail-title h2", timeout=5000)
            check("base.title", page.locator(".detail-title h2").inner_text() == "愚者")
            freshness = page.locator("#freshness").inner_text()
            check("base.freshness", "概览" in freshness and "列表" in freshness, freshness)
            check("base.date", "2026-11-21" in freshness, freshness)
            check(
                "base.freshness_values",
                "概览 —" not in freshness and "列表 —" not in freshness,
                freshness,
            )

            page.click("#refresh")
            page.wait_for_selector("#error-detail:not(.hidden)", timeout=5000)
            check(
                "detail.old_kept",
                page.locator(".detail-title h2").inner_text() == "愚者",
            )
            check(
                "detail.error_text",
                "刷新失败" in page.locator("#error-detail").inner_text(),
            )
            page.click("#error-detail .error-retry")
            page.wait_for_function(
                "document.querySelector('.detail-title h2')?.textContent === '魔术师'",
                timeout=5000,
            )
            check("detail.retry", page.locator("#error-detail").is_hidden())
            page.close()

            page = open_page(browser, "cross")
            page.wait_for_selector(".detail-title h2", timeout=5000)
            page.locator(".user").nth(1).click()
            page.wait_for_selector("#daily-detail .empty", timeout=5000)
            check(
                "cross.message",
                "该用户读取失败" in page.locator("#daily-detail .empty").inner_text(),
            )
            check(
                "cross.no_old",
                page.locator(".detail-title h2").count() == 0,
            )
            check(
                "cross.retry",
                page.locator("#error-detail .error-retry").is_visible(),
            )
            page.close()

            page = open_page(browser, "cards")
            page.wait_for_selector(".user", timeout=5000)
            page.click("#tab-cards")
            page.click("#tab-cards")
            page.wait_for_timeout(200)
            check(
                "cards.loading",
                "牌面加载中" in page.locator("#card-grid").inner_text(),
            )
            page.wait_for_selector("#error-cards:not(.hidden)", timeout=3000)
            check(
                "cards.error_text",
                "牌面加载失败" in page.locator("#error-cards").inner_text(),
            )
            calls = page.evaluate("window.__calls.cards")
            check("cards.dedupe", calls == 1, calls)
            page.click("#error-cards .error-retry")
            page.wait_for_selector(".card-cell", timeout=5000)
            check("cards.retry", page.locator("#error-cards").is_hidden())
            check(
                "cards.freshness",
                "牌面" in page.locator("#freshness").inner_text(),
            )
            page.close()

            page = open_page(browser, "preview")
            page.wait_for_selector(".user", timeout=5000)
            page.click("#tab-preview")
            page.wait_for_timeout(200)
            check(
                "preview.loading",
                page.locator("#preview-spread").is_disabled(),
            )
            check(
                "preview.loading_text",
                "牌阵加载中" in page.locator("#preview-spread").inner_text(),
            )
            page.click("#preview-draw")
            check(
                "preview.blocked",
                "牌阵还在加载" in page.locator("#toast").inner_text(),
            )
            page.wait_for_function(
                "!document.querySelector('#preview-spread').disabled"
                " && document.querySelectorAll('#preview-spread option').length > 0",
                timeout=5000,
            )
            check(
                "preview.ready",
                page.locator("#preview-spread option").first.get_attribute("value")
                == "羽签",
            )
            page.close()

            page = open_page(browser, "lightbox")
            page.wait_for_selector(".user", timeout=5000)
            page.click("#tab-cards")
            page.wait_for_selector(".card-cell", timeout=5000)
            page.click(".card-cell")
            page.wait_for_selector("#card-modal:not(.hidden)", timeout=3000)
            page.wait_for_timeout(300)
            page.click("#card-modal-img")
            page.wait_for_function(
                "document.querySelector('#lightbox-status')?.textContent"
                " === '大图加载失败'",
                timeout=5000,
            )
            check(
                "lightbox.retry_visible",
                page.locator("#lightbox-retry").is_visible(),
            )
            page.click("#lightbox-retry")
            page.wait_for_selector("#lightbox-img[src^='data:']", timeout=3000)
            check("lightbox.retry", page.locator("#lightbox-status").is_hidden())
            page.close()

            page = open_page(browser, "decode")
            page.wait_for_selector(".user", timeout=5000)
            page.click("#tab-cards")
            page.wait_for_selector(".card-cell", timeout=5000)
            page.click(".card-cell")
            page.wait_for_selector("#card-modal:not(.hidden)", timeout=3000)
            page.wait_for_timeout(300)
            page.click("#card-modal-img")
            page.wait_for_function(
                "document.querySelector('#lightbox-status')?.textContent"
                " === '大图加载失败'",
                timeout=5000,
            )
            check(
                "decode.retry_visible",
                page.locator("#lightbox-retry").is_visible(),
            )
            page.close()

            page = open_page(browser, "slow")
            page.wait_for_selector(".user", timeout=5000)
            page.click("#refresh")
            page.wait_for_timeout(200)
            check(
                "refresh.busy",
                page.get_attribute("#refresh", "aria-busy") == "true",
            )
            check(
                "refresh.label",
                "刷新中" in page.locator("#refresh").inner_text(),
            )
            page.wait_for_timeout(1600)
            check(
                "refresh.done",
                page.get_attribute("#refresh", "aria-busy") == "false",
            )
            check(
                "refresh.toast",
                page.locator("#toast").inner_text() == "已刷新",
            )
            page.close()

            page = open_page(browser, "empty")
            page.wait_for_selector("#daily-list .empty", timeout=5000)
            check(
                "empty.text",
                "在聊天里抽一签" in page.locator("#daily-list").inner_text(),
            )
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
