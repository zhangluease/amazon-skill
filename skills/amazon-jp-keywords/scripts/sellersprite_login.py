"""Open SellerSprite's login page in an isolated desktop browser."""

from contextlib import contextmanager, suppress
import os
import subprocess
import sys
import sysconfig
import time
from urllib.parse import urlsplit

from amazon_config import browser_status
from environment import CACHE, SetupError, prepare_package


ORIGIN = "https://www.sellersprite.com"
LOGIN_URL = ORIGIN + "/cn/w/user/login"
LOGIN_TIMEOUT = 300
BROWSER_DEPENDENCY = "playwright==1.57.0" if sys.version_info < (3, 10) else "playwright==1.65.2"


class LoginError(Exception):
    """Login failure that can be shown without revealing browser state."""


def close_owned_browser(browser, error_type):
    """Finish each context before closing this script's isolated browser."""
    try:
        for context in browser.contexts:
            with suppress(error_type):
                context.close()
    finally:
        browser.close()


def launch_browser(chromium, error_type, packages, *, prepare):
    for channel in ("chrome", "msedge"):
        try:
            return chromium.launch(channel=channel, headless=False, timeout=15000), channel
        except error_type:
            pass
    if not os.path.isfile(chromium.executable_path):
        if not prepare:
            raise LoginError("SELLERSPRITE_BROWSER_MISSING")
        env = {**os.environ, "PYTHONPATH": str(packages)}
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"],
                env=env, check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            raise LoginError("SELLERSPRITE_BROWSER_DOWNLOAD_FAILED") from None
    try:
        return chromium.launch(headless=False, timeout=15000), "chromium"
    except error_type:
        raise LoginError("SELLERSPRITE_BROWSER_LAUNCH_FAILED") from None


@contextmanager
def desktop_browser(*, prepare):
    if (sys.platform.startswith("linux") and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))
            or sys.platform == "win32" and os.getenv("SESSIONNAME", "").lower() == "services"):
        raise LoginError("SELLERSPRITE_DESKTOP_REQUIRED")
    try:
        settings = browser_status()
    except (OSError, ValueError):
        raise LoginError("SELLERSPRITE_BROWSER_CONFIG_INVALID") from None
    packages = CACHE / "python" / BROWSER_DEPENDENCY.replace("==", "-")
    packages /= f"{sys.implementation.cache_tag}-{sysconfig.get_platform()}"
    if not prepare and not packages.is_dir():
        raise LoginError("SELLERSPRITE_PLAYWRIGHT_MISSING")
    try:
        prepare_package(BROWSER_DEPENDENCY, packages)
        from playwright.sync_api import Error, sync_playwright
    except (ImportError, OSError, SetupError):
        raise LoginError("SELLERSPRITE_PLAYWRIGHT_SETUP_FAILED") from None

    previous_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = settings["cache_dir"]
    try:
        with sync_playwright() as playwright:
            browser, engine = launch_browser(playwright.chromium, Error, packages, prepare=prepare)
            try:
                yield browser, {**settings, "engine": engine, "version": browser.version, "headed": True}
            except Error:
                if not browser.is_connected() or not any(context.pages for context in browser.contexts):
                    raise LoginError("SELLERSPRITE_LOGIN_CANCELLED") from None
                raise LoginError("SELLERSPRITE_BROWSER_PAGE_FAILED") from None
            finally:
                with suppress(Error):
                    close_owned_browser(browser, Error)
    except (Error, OSError):
        raise LoginError("SELLERSPRITE_PLAYWRIGHT_DRIVER_FAILED") from None
    finally:
        if previous_path is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = previous_path


def check_browser(*, prepare=False):
    """Launch and close a local window without contacting SellerSprite."""
    with desktop_browser(prepare=prepare) as (browser, settings):
        page = browser.new_page()
        page.set_content("<title>amazon-skill-browser-check</title>", timeout=10000)
        if page.title() != "amazon-skill-browser-check":
            raise LoginError("SELLERSPRITE_BROWSER_PAGE_FAILED")
        return {"status": "ready", "launch_verified": True, **settings}


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
    with desktop_browser(prepare=True) as (browser, _):
        return wait_for_login(browser, endpoint, fetch_page)
