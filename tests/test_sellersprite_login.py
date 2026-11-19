import contextlib
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/amazon-jp-keywords/scripts"
sys.path.insert(0, str(SCRIPTS))
import sellersprite_login as login


class BrowserError(Exception):
    pass


class SellerSpriteLoginCleanupTests(unittest.TestCase):
    def fixture(self, *, context_failure=False, browser_failure=False):
        events = []

        class Context:
            pages = [object()]

            def __init__(self, name):
                self.name = name

            def close(self):
                events.append(self.name)
                if context_failure and self.name == "first-context":
                    raise BrowserError("fixture context failure")

        class Browser:
            version = "fixture"
            contexts = [Context("first-context"), Context("second-context")]

            def is_connected(self):
                return True

            def close(self):
                events.append("browser")
                if browser_failure:
                    raise BrowserError("fixture browser failure")

        @contextlib.contextmanager
        def sync_playwright():
            try:
                yield types.SimpleNamespace(chromium=object())
            finally:
                events.append("driver")

        package = types.ModuleType("playwright")
        package.__path__ = []
        api = types.ModuleType("playwright.sync_api")
        api.Error, api.sync_playwright = BrowserError, sync_playwright
        browser = Browser()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": api}))
        stack.enter_context(patch.dict(os.environ, {
            "DISPLAY": ":fixture", "SESSIONNAME": "fixture",
            "PLAYWRIGHT_BROWSERS_PATH": "/fixture/original-cache"}))
        stack.enter_context(patch.object(login, "browser_status", return_value={"cache_dir": "/fixture/cache"}))
        stack.enter_context(patch.object(login, "prepare_package"))
        launcher = stack.enter_context(patch.object(login, "launch_browser", return_value=(browser, "fixture")))
        return browser, events, launcher

    def assert_cleaned(self, events):
        self.assertEqual(events, ["first-context", "second-context", "browser", "driver"])
        self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], "/fixture/original-cache")

    def test_success_finishes_contexts_before_browser_and_driver(self):
        browser, events, _ = self.fixture()
        with login.desktop_browser(prepare=True) as (opened, settings):
            self.assertIs(opened, browser)
            self.assertEqual(settings["engine"], "fixture")
            self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], "/fixture/cache")
            self.assertEqual(events, [])
        self.assert_cleaned(events)

    def test_task_error_is_preserved_after_cleanup(self):
        _, events, _ = self.fixture()
        failure = ValueError("fixture task failure")
        with self.assertRaises(ValueError) as caught:
            with login.desktop_browser(prepare=True):
                raise failure
        self.assertIs(caught.exception, failure)
        self.assert_cleaned(events)

    def test_page_error_still_closes_contexts_and_preserves_error_category(self):
        _, events, _ = self.fixture()
        with self.assertRaisesRegex(login.LoginError, "^SELLERSPRITE_BROWSER_PAGE_FAILED$"):
            with login.desktop_browser(prepare=True):
                raise BrowserError("fixture page failure")
        self.assert_cleaned(events)

    def test_keyboard_interrupt_still_closes_contexts(self):
        _, events, _ = self.fixture()
        with self.assertRaises(KeyboardInterrupt):
            with login.desktop_browser(prepare=True):
                raise KeyboardInterrupt()
        self.assert_cleaned(events)

    def test_context_failure_does_not_prevent_remaining_cleanup(self):
        _, events, _ = self.fixture(context_failure=True)
        with login.desktop_browser(prepare=True):
            pass
        self.assert_cleaned(events)

    def test_browser_close_failure_does_not_replace_task_error(self):
        _, events, _ = self.fixture(browser_failure=True)
        failure = ValueError("fixture task failure")
        with self.assertRaises(ValueError) as caught:
            with login.desktop_browser(prepare=True):
                raise failure
        self.assertIs(caught.exception, failure)
        self.assert_cleaned(events)

    def test_launch_failure_releases_driver_and_restores_environment(self):
        _, events, launcher = self.fixture()
        launcher.side_effect = login.LoginError("SELLERSPRITE_BROWSER_LAUNCH_FAILED")
        with self.assertRaisesRegex(login.LoginError, "^SELLERSPRITE_BROWSER_LAUNCH_FAILED$"):
            with login.desktop_browser(prepare=True):
                self.fail("Launch failed")
        self.assertEqual(events, ["driver"])
        self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], "/fixture/original-cache")


if __name__ == "__main__":
    unittest.main()
