import contextlib
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/amazon-jp-keywords/scripts"
sys.path.insert(0, str(SCRIPTS))
import sellersprite_keyword_miner as miner


class KeywordMinerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = self.root / "private" / "session.json"
        patcher = patch.object(miner, "SESSION_FILE", self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def capture(self, url=miner.ENDPOINT, market=6):
        request = self.root / "request.txt"
        request.write_text(shlex.join([
            "curl", "--url", url, "-b", "session=test-only",
            "-H", "User-Agent: fixture", "-H", "Authorization: unrelated",
            "--data-raw", json.dumps({"keyword": "スピーカー", "market": market}),
        ]), encoding="utf-8")
        return request

    def page(self, seed, number, session):
        return {"page": number, "size": 50, "total": 1981, "items": [
            {"keyword": f"スピーカー {number}-{index}", "searches": 0, "month": "2026.10"}
            for index in range(50)
        ]}

    def test_session_import_keeps_only_required_headers_and_checks_endpoint(self):
        miner.import_session(self.capture())
        self.assertEqual(json.loads(self.session.read_text()), {
            "Cookie": "session=test-only", "User-Agent": "fixture"})
        self.assertEqual(self.session.stat().st_mode & 0o777, 0o600)
        for url, market in [("https://other.invalid/v3/api/keyword-miner", 6), (miner.ENDPOINT, 1)]:
            with self.assertRaises(miner.QueryError):
                miner.import_session(self.capture(url, market))

    def test_three_pages_preserve_metrics_and_coverage(self):
        miner.import_session(self.capture())
        with patch.object(miner, "request_page", side_effect=self.page) as request, \
                patch.object(miner.time, "sleep"), patch.object(miner, "write_xlsx") as export, \
                patch.object(miner, "login_and_fetch") as login:
            result = miner.collect(["スピーカー"], 3, self.root / "result")
        login.assert_not_called()
        self.assertEqual([call.args[1] for call in request.call_args_list], [1, 2, 3])
        self.assertEqual(result["count"], 150)
        self.assertTrue(result["queries"][0]["has_more"])
        data = json.loads((self.root / "result" / "sellersprite-keywords.json").read_text())
        self.assertEqual(data["rows"][0][6], 0)
        self.assertIsNone(data["rows"][0][7])
        self.assertEqual(len(export.call_args.args[2]), 150)
        self.assertNotIn("test-only", json.dumps(data))

    def test_rate_limit_retains_first_page_and_stops_remaining_seeds(self):
        miner.import_session(self.capture())
        first = self.page("スピーカー", 1, {})
        with patch.object(miner, "request_page", side_effect=[first, miner.QueryError("HTTP_429")]) as request, \
                patch.object(miner.time, "sleep"), patch.object(miner, "write_xlsx"), \
                patch.object(miner, "login_and_fetch") as login:
            result = miner.collect(["スピーカー", "水筒"], 3, self.root / "result")
        login.assert_not_called()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["count"], 50)
        self.assertEqual(result["queries"][1]["status"], "skipped")

    def test_missing_session_prompts_once_then_reuses_saved_login(self):
        session = {"Cookie": "session=fresh", "User-Agent": "fixture"}

        def finish_login(endpoint, fetch):
            self.assertEqual(endpoint, miner.ENDPOINT)
            return session, fetch(session)

        with patch.object(miner, "login_and_fetch", side_effect=finish_login) as login, \
                patch.object(miner, "request_page", side_effect=self.page) as request, \
                patch.object(miner, "write_xlsx"):
            result = miner.collect(["スピーカー"], 1, self.root / "first")
            again = miner.collect(["スピーカー"], 1, self.root / "second")
        self.assertEqual((result["count"], again["count"]), (50, 50))
        login.assert_called_once()
        self.assertEqual([call.args[2] for call in request.call_args_list], [session, session])
        self.assertEqual(miner.load_session(), session)
        self.assertEqual(self.session.stat().st_mode & 0o777, 0o600)

    def test_expired_session_retries_failed_page_without_repeating_first_page(self):
        miner.import_session(self.capture())
        refreshed = {"Cookie": "session=refreshed", "User-Agent": "fixture"}

        def query(seed, page, session):
            if page == 2 and session != refreshed:
                raise miner.QueryError("HTTP_403")
            return self.page(seed, page, session)

        def finish_login(endpoint, fetch):
            return refreshed, fetch(refreshed)

        with patch.object(miner, "login_and_fetch", side_effect=finish_login) as login, \
                patch.object(miner, "request_page", side_effect=query) as request, \
                patch.object(miner.time, "sleep"), patch.object(miner, "write_xlsx"):
            result = miner.collect(["スピーカー"], 3, self.root / "result")
        login.assert_called_once()
        self.assertEqual([call.args[1] for call in request.call_args_list], [1, 2, 2, 3])
        self.assertEqual(result["count"], 150)
        self.assertEqual(miner.load_session(), refreshed)

    def test_cancelling_login_preserves_first_page_and_previous_session(self):
        miner.import_session(self.capture())
        previous = miner.load_session()
        first = self.page("スピーカー", 1, previous)
        error = miner.LoginError("SELLERSPRITE_LOGIN_CANCELLED")
        with patch.object(miner, "login_and_fetch", side_effect=error) as login, \
                patch.object(miner, "request_page", side_effect=[first, miner.QueryError("HTTP_401")]), \
                patch.object(miner.time, "sleep"), patch.object(miner, "write_xlsx"):
            result = miner.collect(["スピーカー", "水筒"], 3, self.root / "result")
        login.assert_called_once()
        self.assertEqual((result["status"], result["count"]), ("partial", 50))
        self.assertEqual(result["queries"][0]["error_code"], str(error))
        self.assertEqual(result["queries"][1]["status"], "skipped")
        self.assertEqual(miner.load_session(), previous)

    def test_repeated_page_is_reported_as_partial(self):
        miner.import_session(self.capture())
        with patch.object(miner, "request_page", return_value=self.page("スピーカー", 1, {})), \
                patch.object(miner.time, "sleep"), patch.object(miner, "write_xlsx"):
            result = miner.collect(["スピーカー"], 3, self.root / "result")
        self.assertEqual(result["count"], 50)
        self.assertEqual(result["queries"][0]["error_code"], "PAGE_OVERLAP")

    def test_page_four_is_rejected_before_network_access(self):
        with patch.object(miner, "request_page") as request, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                miner.main(["search", "--pages", "4", "--out", str(self.root), "スピーカー"])
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
