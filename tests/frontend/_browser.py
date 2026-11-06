"""三条前端 smoke 共用的浏览器启动。

本机优先用 Edge（与主人日常看到的一致）；CI 没装 Edge，就退回 Playwright
自带的 chromium。两边跑的是同一份脚本，差别只在浏览器。
"""


def launch(playwright):
    try:
        return playwright.chromium.launch(channel="msedge", headless=True)
    except Exception:  # 没装 Edge 时 Playwright 直接抛 Error
        return playwright.chromium.launch(headless=True)
