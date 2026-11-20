"""本地 smoke：弹窗层级、焦点管理与折叠牌阵可访问性。

运行：
    python tests/frontend/modal_focus_smoke.py

不依赖 AstrBot：注入假 bridge，覆盖：
- 牌义弹窗打开即有骨架，焦点移入并困住，背景 inert；
- Esc 先关灯箱、再关牌义弹窗，焦点逐层归还；
- 图片按钮键盘可打开大图，灯箱有独立关闭按钮；
- 灯箱打开时下层弹窗不再同时 aria-modal；
- 折叠牌阵的 aria-expanded / aria-hidden / aria-controls 一致。

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
  const px = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const card = (id, cn, num) => ({ id, cn, suit: "major", num });
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
        uid: "u1", card_id: "major_00", card_cn: "愚者", upright: true,
        spirit: "", topics: 1, stale: false,
      }],
      indexed_total: 1, shown: 1, truncated: false,
    },
    "daily/detail": {
      uid: "u1", date: "20261121", card: card("major_00", "愚者", "0"),
      upright: true, stale: false, persona: "off", spirit: "",
      topics: [{ topic: "测试", text: "测试解读" }],
    },
    "cards": { items: [{ id: "major_00", cn: "愚者", en: "The Fool", suit: "major" }] },
    "card-detail": {
      id: "major_00", cn: "愚者", en: "The Fool", suit: "major", num: "0",
      up: { keywords: ["开始"], what: "测试", how: "测试" },
      down: { keywords: ["冲动"], what: "测试", turn: "测试", energy: "测试" },
    },
    "spreads": {
      items: [
        { name: "羽签", scenario: "测试一", positions: [{ name: "当下", meaning: "" }] },
        { name: "羽时三刻", scenario: "测试二", positions: [{ name: "过去", meaning: "" }] },
      ],
    },
  };
  window.AstrBotPluginView = {
    ready: async () => {},
    t: (_key, fallback) => fallback,
    apiGet: async (endpoint) => {
      if (endpoint === "card-detail") await wait(600);
      if (endpoint === "card-image") return { mime: "image/png", base64: px };
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

    try:
        with sync_playwright() as p:
            browser = launch(p)
            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.add_init_script(FAKE_BRIDGE)
            page.goto(base + "/", wait_until="load")
            page.wait_for_selector(".user", timeout=5000)
            page.click("#tab-cards")
            page.wait_for_selector(".card-cell", timeout=5000)

            page.focus(".card-cell")
            page.keyboard.press("Enter")
            page.wait_for_selector("#card-modal:not(.hidden)", timeout=2000)
            check(
                "modal.skeleton",
                page.locator("#card-modal-body .skeleton-line").count() >= 2,
            )
            check(
                "modal.focus",
                page.evaluate("document.activeElement.id") == "card-modal-close",
            )
            check(
                "modal.background_inert",
                page.evaluate("document.querySelector('main').hasAttribute('inert')"),
            )
            check(
                "modal.name",
                page.get_attribute("#card-modal", "aria-labelledby")
                == "card-modal-name",
            )
            check(
                "modal.image_disabled",
                page.locator("#card-modal-open").is_disabled(),
            )
            check(
                "modal.orient_disabled",
                page.locator("#card-orient button").first.is_disabled(),
            )

            page.wait_for_function(
                "document.querySelectorAll('#card-modal-body .meaning').length > 0",
                timeout=3000,
            )
            check(
                "modal.image_enabled",
                not page.locator("#card-modal-open").is_disabled(),
            )
            check(
                "modal.orient_enabled",
                not page.locator("#card-orient button").first.is_disabled(),
            )
            page.focus("#card-modal-close")
            page.keyboard.press("Tab")
            check(
                "modal.tab_moves",
                page.evaluate(
                    "document.activeElement.id !== 'card-modal-close'"
                    " && Boolean(document.activeElement.closest('#card-modal'))"),
            )
            page.focus("#card-modal-close")
            page.keyboard.press("Shift+Tab")
            check(
                "modal.shift_tab_moves",
                page.evaluate(
                    "document.activeElement.id !== 'card-modal-close'"
                    " && Boolean(document.activeElement.closest('#card-modal'))"),
            )
            check(
                "modal.image_button",
                page.get_attribute("#card-modal-open", "aria-label") == "查看大图",
            )

            page.focus("#card-modal-open")
            page.keyboard.press("Enter")
            page.wait_for_selector("#lightbox:not(.hidden)", timeout=2000)
            check(
                "lightbox.focus",
                page.evaluate("document.activeElement.id") == "lightbox-close",
            )
            check(
                "lightbox.modal_inert",
                page.evaluate(
                    "document.querySelector('#card-modal').hasAttribute('inert')"),
            )
            check(
                "lightbox.single_modal",
                page.get_attribute("#card-modal", "aria-modal") == "false",
            )
            check(
                "lightbox.name",
                page.get_attribute("#lightbox", "aria-label") == "牌面大图",
            )

            page.keyboard.press("Escape")
            page.wait_for_selector("#lightbox.hidden", state="attached", timeout=2000)
            check(
                "esc.lightbox_focus",
                page.evaluate("document.activeElement.id") == "card-modal-open",
            )
            check(
                "esc.modal_open",
                page.locator("#card-modal").is_visible(),
            )
            check(
                "esc.modal_aria",
                page.get_attribute("#card-modal", "aria-modal") == "true",
            )

            page.focus("#card-modal-open")
            page.keyboard.press("Enter")
            page.wait_for_selector("#lightbox:not(.hidden)", timeout=2000)
            page.click("#lightbox-close")
            page.wait_for_selector("#lightbox.hidden", state="attached", timeout=2000)
            check(
                "lightbox.close_button",
                page.locator("#card-modal").is_visible()
                and page.locator("#lightbox").is_hidden(),
            )

            page.keyboard.press("Escape")
            page.wait_for_selector("#card-modal.hidden", state="attached", timeout=2000)
            check(
                "esc.modal_focus",
                page.evaluate(
                    "document.activeElement.classList.contains('card-cell')"),
            )
            check(
                "esc.background",
                not page.evaluate(
                    "document.querySelector('main').hasAttribute('inert')"),
            )

            page.click("#tab-spreads")
            page.wait_for_selector(".spread-card", timeout=5000)
            first = page.locator(".spread-card").nth(0)
            second = page.locator(".spread-card").nth(1)
            first.locator(".spread-toggle").click()
            check(
                "spread.open",
                first.locator(".spread-toggle").get_attribute("aria-expanded") == "true"
                and first.locator(".spread-body").get_attribute("aria-hidden") == "false",
            )
            body_id = first.locator(".spread-body").get_attribute("id")
            check("spread.body_id", bool(body_id), body_id)
            check(
                "spread.controls",
                body_id is not None
                and first.locator(".spread-toggle").get_attribute("aria-controls")
                == body_id,
            )
            second.locator(".spread-toggle").click()
            check(
                "spread.previous_closed",
                first.locator(".spread-toggle").get_attribute("aria-expanded") == "false"
                and first.locator(".spread-body").get_attribute("aria-hidden") == "true",
            )
            check(
                "spread.second_open",
                second.locator(".spread-toggle").get_attribute("aria-expanded") == "true"
                and second.locator(".spread-body").get_attribute("aria-hidden") == "false",
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
