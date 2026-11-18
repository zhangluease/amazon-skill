"""Open SellerSprite's login page in an isolated desktop browser."""

import os
import subprocess
import sys
import sysconfig
import time
from urllib.parse import urlsplit

from environment import CACHE, SetupError, prepare_package


ORIGIN = "https://www.sellersprite.com"
LOGIN_URL = ORIGIN + "/cn/w/user/login"
LOGIN_TIMEOUT = 300
BROWSER_DEPENDENCY = "playwright==1.57.0" if sys.version_info < (3, 10) else "playwright==1.65.2"


class LoginError(Exception):
    """Login failure that can be shown without revealing browser state."""


def launch_browser(chromium, error_type, packages):
    for channel in ("chrome", "msedge"):
        try:
            return chromium.launch(channel=channel, headless=False, timeout=15000)
        except error_type:
            pass
    if not os.path.isfile(chromium.executable_path):
        env = {**os.environ, "PYTHONPATH": str(packages)}
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"],
                env=env, check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            raise LoginError("SELLERSPRITE_BROWSER_SETUP_FAILED") from None
    return chromium.launch(headless=False, timeout=15000)


def wait_for_login(browser, endpoint, fetch_page):
    page = browser.new_page(locale="zh-CN")
    user_agent = page.evaluate("navigator.userAgent")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.bring_to_front()
    print("请在弹出的卖家精灵窗口完成登录。登录成功后会自动继续查词。",
          file=sys.stderr, flush=True)

    deadline = time.monotonic() + LOGIN_TIMEOUT
    last_cookie = ""
    while time.monotonic() < deadline:
        if page.is_closed():
            raise LoginError("SELLERSPRITE_LOGIN_CANCELLED")
        address = urlsplit(page.url)
        if (address.scheme == "https" and address.netloc == urlsplit(ORIGIN).netloc
                and "/w/user/" not in address.path):
            cookies = page.context.cookies(endpoint)
            cookies.sort(key=lambda cookie: -len(cookie["path"]))
            cookie_header = "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)
            if cookie_header and cookie_header != last_cookie:
                last_cookie = cookie_header
                session = {"Cookie": cookie_header, "User-Agent": user_agent}
                data = fetch_page(session)
                if data is not None:
                    return session, data
        page.wait_for_timeout(1000)
    raise LoginError("SELLERSPRITE_LOGIN_TIMEOUT")


def login_and_fetch(endpoint, fetch_page):
    """Check the session by fetching the pending page once login completes."""
    if sys.platform.startswith("linux") and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
        raise LoginError("SELLERSPRITE_DESKTOP_REQUIRED")
    packages = CACHE / "python" / BROWSER_DEPENDENCY.replace("==", "-")
    packages = packages / f"{sys.implementation.cache_tag}-{sysconfig.get_platform()}"
    try:
        prepare_package(BROWSER_DEPENDENCY, packages)
        from playwright.sync_api import Error, sync_playwright
    except (ImportError, OSError, SetupError):
        raise LoginError("SELLERSPRITE_BROWSER_SETUP_FAILED") from None

    browser = None
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright.chromium, Error, packages)
            try:
                return wait_for_login(browser, endpoint, fetch_page)
            except Error:
                if not browser.is_connected() or not any(context.pages for context in browser.contexts):
                    raise LoginError("SELLERSPRITE_LOGIN_CANCELLED") from None
                raise
            finally:
                try:
                    browser.close()
                except Error:
                    pass
    except (Error, OSError):
        raise LoginError("SELLERSPRITE_BROWSER_ERROR") from None
