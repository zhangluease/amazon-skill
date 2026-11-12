import copy
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills/amazon-jp-keywords/scripts/aba_to_table.py"
SPEC = importlib.util.spec_from_file_location("aba_to_table", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def page(**changes):
    value = {
        "queryId": "query-one", "reportId": "report-one",
        "reportType": MODULE.REPORT_TYPE, "marketplaceId": "A1VC38T7YXB528",
        "status": "DONE", "coverageComplete": True, "rowsMatched": 1,
        "dataStartTime": "2026-11-01T00:00:00Z", "dataEndTime": "2026-11-07T23:59:59Z",
        "cursor": None, "nextCursor": None,
        "rows": [{"searchTerm": "加湿器 フィルター", "departmentName": "Amazon.co.jp",
                  "searchFrequencyRank": 123, "clickedAsin": "B123456789",
                  "clickShareRank": 1, "clickShare": 0, "conversionShare": None,
                  "matchedAsins": ["B123456789"], "matchedSeedTerms": ["加湿器"]}],
    }
    value.update(changes)
    return value


def convert(*pages):
    return MODULE.convert({"queries": [{"reportPeriod": "WEEK", "pages": list(pages)}]})


class AbaTableTests(unittest.TestCase):
    def test_preserves_japanese_zero_and_missing_shares(self):
        result = convert(page())
        self.assertEqual(result["rows"][0][0], "加湿器 フィルター")
        self.assertEqual(result["rows"][0][6], 0)
        self.assertIsNone(result["rows"][0][7])
        self.assertEqual(result["query_statuses"][0]["status"], "complete")
        self.assertEqual(len(result["rows"][0]), len(result["headers"]))

    def test_all_pages_are_required_and_ordered(self):
        first = page(rowsMatched=2, nextCursor="page-two")
        second = page(rowsMatched=2, cursor="page-two")
        second["rows"][0]["clickedAsin"] = "B987654321"
        self.assertEqual(len(convert(first, second)["rows"]), 2)
        self.assertEqual(convert(first)["query_statuses"][0]["status"], "partial")
        for invalid in [(second,), (first, first), (page(), second), (page(rowsMatched=2),)]:
            with self.assertRaises(ValueError):
                convert(*invalid)

    def test_only_complete_empty_queries_mean_no_matches(self):
        self.assertEqual(convert(page(rows=[], rowsMatched=0))["query_statuses"][0]["status"], "no_matches")
        for state in ["IN_PROGRESS", "FAILED"]:
            result = convert(page(status=state, coverageComplete=False, rows=[], rowsMatched=0))
            self.assertNotEqual(result["query_statuses"][0]["status"], "no_matches")
            self.assertEqual(result["rows"], [])
        result = convert(page(status="PARTIAL", coverageComplete=False))
        self.assertEqual(result["query_statuses"][0]["status"], "partial")

    def test_overlapping_batches_merge_reasons_but_departments_remain_distinct(self):
        first, second = page(), page(queryId="query-two")
        second["rows"][0]["matchedSeedTerms"] = ["フィルター"]
        result = MODULE.convert({"queries": [{"pages": [first]}, {"pages": [second]}]})
        self.assertEqual(len(result["rows"]), 1)
        self.assertIn("フィルター", result["rows"][0][12])
        second["rows"][0]["departmentName"] = "Home"
        self.assertEqual(len(MODULE.convert({"queries": [{"pages": [first]}, {"pages": [second]}]})["rows"]), 2)

    def test_conflicting_metrics_and_wrong_scope_fail(self):
        first, second = page(), page(queryId="query-two")
        second["rows"][0]["clickShare"] = 0.1
        with self.assertRaises(ValueError):
            MODULE.convert({"queries": [{"pages": [first]}, {"pages": [second]}]})
        for changes in [{"marketplaceId": "US"}, {"reportType": "SQP"}]:
            with self.assertRaises(ValueError):
                convert(page(**changes))
        invalid = copy.deepcopy(first)
        invalid["rows"][0]["clickShare"] = 7.71
        with self.assertRaises(ValueError):
            convert(invalid)


if __name__ == "__main__":
    unittest.main()
