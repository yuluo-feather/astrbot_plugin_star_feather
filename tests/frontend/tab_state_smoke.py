"""本地 smoke：观测台页签的 URL、ARIA、键盘与滚动复位。

运行：
    python tests/frontend/tab_state_smoke.py

不依赖 AstrBot：复用 host_contract 的假 bridge，验证：
- `?tab=` 初始化、点击写 URL、刷新保持、前进/后退恢复、非法值回 daily；
- role=tab / aria-selected / aria-hidden 与可见面板一致；
- ←/→/Home/End 切换并移动焦点；
- 切页时复位 `.list` 内层滚动。

需要本机安装 Playwright 与 Edge；缺少依赖时返回 2，不静默通过。
"""
import functools
import http.server
import sys
import threading
from pathlib import Path

DEFAULT_VIEW_DIR = Path(__file__).resolve().parents[2] / "views" / "star-feather"


def main():
    view_dir = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_VIEW_DIR
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed")
        return 2
    from _browser import launch
    from host_contract_smoke import FAKE_BRIDGE
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

    def tab_state(page):
        return page.evaluate(
            "() => {"
            "  const tabs = [...document.querySelectorAll('.tab')];"
            "  const active = tabs.filter((tab) => tab.classList.contains('active'));"
            "  const selected = tabs.filter("
            "    (tab) => tab.getAttribute('aria-selected') === 'true');"
            "  const visible = [...document.querySelectorAll('.panel')]"
            "    .filter((panel) => !panel.classList.contains('hidden'))"
            "    .map((panel) => panel.id);"
            "  const hidden = [...document.querySelectorAll('.panel')]"
            "    .filter((panel) => panel.classList.contains('hidden'))"
            "    .every((panel) => panel.getAttribute('aria-hidden') === 'true');"
            "  return {"
            "    active: active.map((tab) => tab.dataset.tab),"
            "    selected: selected.map((tab) => tab.dataset.tab),"
            "    visible, hidden,"
            "    urlTab: new URL(location.href).searchParams.get('tab'),"
            "  };"
            "}"
        )

    try:
        with sync_playwright() as p:
            browser = launch(p)

            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.add_init_script(FAKE_BRIDGE)
            page.goto(base + "/?tab=cards", wait_until="load")
            page.wait_for_selector(".card-cell", timeout=5000)
            state = tab_state(page)
            check("initial.active", state["active"] == ["cards"], state)
            check("initial.selected", state["selected"] == ["cards"], state)
            check("initial.visible", state["visible"] == ["panel-cards"], state)
            check("initial.aria_hidden", state["hidden"], state)
            check("initial.url", state["urlTab"] == "cards", state)
            page.close()

            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.add_init_script(FAKE_BRIDGE)
            page.goto(base + "/?tab=nope", wait_until="load")
            page.wait_for_selector(".user", timeout=5000)
            state = tab_state(page)
            check("invalid.active", state["active"] == ["daily"], state)
            check("invalid.url", state["urlTab"] is None, state)
            page.close()

            page = browser.new_page(viewport={"width": 1180, "height": 900})
            page.add_init_script(FAKE_BRIDGE)
            page.goto(base + "/", wait_until="load")
            page.wait_for_selector(".user", timeout=5000)

            page.click('#tab-cards')
            page.wait_for_selector(".card-cell", timeout=5000)
            check("click.cards_url", tab_state(page)["urlTab"] == "cards")
            page.click('#tab-spreads')
            page.wait_for_selector(".spread-card", timeout=5000)
            check("click.spreads_url", tab_state(page)["urlTab"] == "spreads")

            page.go_back()
            page.wait_for_selector(".card-cell", timeout=5000)
            state = tab_state(page)
            check("back.cards", state["active"] == ["cards"], state)
            check("back.url", state["urlTab"] == "cards", state)

            page.go_forward()
            page.wait_for_selector(".spread-card", timeout=5000)
            state = tab_state(page)
            check("forward.spreads", state["active"] == ["spreads"], state)
            check("forward.url", state["urlTab"] == "spreads", state)

            page.reload(wait_until="load")
            page.wait_for_selector(".spread-card", timeout=5000)
            state = tab_state(page)
            check("reload.spreads", state["active"] == ["spreads"], state)
            check("reload.url", state["urlTab"] == "spreads", state)

            page.focus("#tab-spreads")
            page.keyboard.press("Home")
            check("key.home", tab_state(page)["active"] == ["daily"])
            page.focus("#tab-daily")
            page.keyboard.press("ArrowRight")
            check("key.right", tab_state(page)["active"] == ["cards"])
            page.focus("#tab-cards")
            page.keyboard.press("End")
            check("key.end", tab_state(page)["active"] == ["preview"])
            page.keyboard.press("ArrowRight")
            check("key.wrap", tab_state(page)["active"] == ["daily"])

            page.click('#tab-daily')
            page.wait_for_selector(".user", timeout=5000)
            page.evaluate(
                "() => {"
                "  const list = document.querySelector('#daily-list');"
                "  const filler = document.createElement('div');"
                "  filler.style.cssText = 'height: 900px; flex: none;';"
                "  list.append(filler);"
                "  list.scrollTop = 120;"
                "}"
            )
            check(
                "scroll.before",
                page.evaluate("document.querySelector('#daily-list').scrollTop") > 0,
            )
            page.click('#tab-cards')
            page.wait_for_selector(".card-cell", timeout=5000)
            page.click('#tab-daily')
            page.wait_for_timeout(300)
            check(
                "scroll.reset",
                page.evaluate("document.querySelector('#daily-list').scrollTop") == 0,
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
