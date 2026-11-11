import contextlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/amazon-jp-listing-asin/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("aba_client_under_test", SCRIPTS / "aba_client.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "test-only-http-client-credential"
QUERY = "c71f6467-49fd-4aa4-996f-025464d6a5aa"


class Reply(io.BytesIO):
    def __init__(self, raw=b"", headers=None, status=200):
        super().__init__(raw)
        self.headers = headers or {"Content-Type": "application/json"}
        self.status = status


class Transport:
    def __init__(self, responder=None):
        self.requests = []
        self.responder = responder

    def open(self, request, timeout):
        body = json.loads(request.data)
        self.requests.append((request, body))
        if body["method"] == "initialize":
            result = {"protocolVersion": MODULE.PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "test"}}
            return Reply(json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode(),
                         {"Content-Type": "application/json", "Mcp-Session-Id": "fixture-session"})
        if body["method"] == "notifications/initialized":
            return Reply(status=202)
        if self.responder:
            return self.responder(request, body)
        data = {"credentialsConfigured": True, "marketplaceId": "A1VC38T7YXB528"}
        return self.tool_reply(body["id"], data)

    @staticmethod
    def tool_reply(request_id, data, is_error=False):
        result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
        if is_error:
            result["isError"] = True
        message = {"jsonrpc": "2.0", "id": request_id, "result": result}
        data_lines = "\n".join("data: " + line for line in json.dumps(message, ensure_ascii=False, indent=2).splitlines())
        return Reply(("event: message\n" + data_lines + "\n\n").encode(), {"Content-Type": "text/event-stream"})


def query_page(cursor=None, next_cursor=None, keyword="水筒", **changes):
    result = {"queryId": QUERY, "reportId": "report-123", "reportType": "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT",
              "marketplaceId": "A1VC38T7YXB528", "status": "DONE", "coverageComplete": True,
              "rowsMatched": 3, "cursor": cursor, "nextCursor": next_cursor,
              "dataStartTime": "2026-11-01", "dataEndTime": "2026-11-07",
              "rows": [{"searchTerm": keyword, "departmentName": "Amazon.co.jp", "searchFrequencyRank": 10,
                        "clickedAsin": "B123456789", "clickShareRank": 1, "clickShare": 0.1,
                        "conversionShare": 0.2, "matchedAsins": [], "matchedSeedTerms": ["水筒"]}]}
    result.update(changes)
    return result


class AbaClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"spapi_mcp": {"access_token": TOKEN}}))

    def test_direct_http_handshake_works_with_screenshot_field_and_no_agent_mcp(self):
        transport = Transport(lambda request, body: Transport.tool_reply(body["id"], {"検索語": "加湿器"}))
        client = MODULE.AbaClient(self.config, opener=transport)
        self.assertEqual(client.call("aba_status"), {"検索語": "加湿器"})
        self.assertEqual(client.config_source, "spapi_mcp")
        self.assertEqual([body["method"] for _request, body in transport.requests],
                         ["initialize", "notifications/initialized", "tools/call"])
        request, body = transport.requests[-1]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(request.get_header("Mcp-session-id"), "fixture-session")
        self.assertNotIn(TOKEN, json.dumps(body))

    def test_missing_or_conflicting_credentials_prevent_network_requests(self):
        transport = Transport()
        self.config.write_text('{}')
        with self.assertRaisesRegex(MODULE.ClientError, "ABA_MCP_ACCESS_TOKEN_MISSING"):
            MODULE.AbaClient(self.config, opener=transport)
        self.config.write_text(json.dumps({"aba_mcp": {"access_token": TOKEN},
                                           "spapi_mcp": {"access_token": "different"}}))
        with self.assertRaisesRegex(MODULE.ClientError, "ABA_MCP_CONFIG_CONFLICT"):
            MODULE.AbaClient(self.config, opener=transport)
        self.assertEqual(transport.requests, [])

    def test_errors_are_redacted_and_create_report_is_not_retried_after_disconnect(self):
        def disconnected(request, body):
            raise urllib.error.URLError(TOKEN)
        transport = Transport(disconnected)
        client = MODULE.AbaClient(self.config, opener=transport)
        with self.assertRaises(MODULE.ClientError) as caught:
            client.call("aba_create_report", {})
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertEqual(len(transport.requests), 3)
        transport = Transport(lambda request, body: Transport.tool_reply(body["id"], {"error": TOKEN}, True))
        with self.assertRaisesRegex(MODULE.ClientError, "MCP_TOOL_FAILED"):
            MODULE.AbaClient(self.config, opener=transport).call("aba_status")

    def test_http_errors_preserve_status_and_retry_after_without_body(self):
        def unauthorized(request, body):
            raise urllib.error.HTTPError(request.full_url, 429, TOKEN, {"Retry-After": "30"}, io.BytesIO(TOKEN.encode()))
        client = MODULE.AbaClient(self.config, opener=Transport(unauthorized))
        with self.assertRaises(MODULE.ClientError) as caught:
            client.call("aba_status")
        self.assertEqual(caught.exception.http_status, 429)
        self.assertEqual(caught.exception.retry_after, 30)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_incomplete_http_bodies_do_not_leak_partial_content(self):
        class BrokenReply(Reply):
            def read(self, *_args):
                raise http.client.IncompleteRead(TOKEN.encode(), 200)
        transport = Transport(lambda *_args: BrokenReply())
        with self.assertRaises(MODULE.ClientError) as caught:
            MODULE.AbaClient(self.config, opener=transport).call("aba_create_report", {})
        self.assertEqual(caught.exception.code, "MCP_NETWORK_ERROR")
        self.assertTrue(caught.exception.outcome_unknown)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_tool_whitelist_and_argument_config_protection(self):
        transport = Transport()
        client = MODULE.AbaClient(self.config, opener=transport)
        for tool, args in [("execute_anything", {}), ("aba_status", {"access_token": TOKEN})]:
            with self.assertRaises(MODULE.ClientError):
                client.call(tool, args)
        self.assertEqual(transport.requests, [])
        for argv in [["status", "--output", str(self.config)],
                     ["call", "--tool", "aba_status", "--arguments-file", str(self.config)]]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = MODULE.main(["--config", str(self.config), *argv])
            self.assertEqual(result, 1)
            self.assertNotIn(TOKEN, output.getvalue())
        self.assertEqual(json.loads(self.config.read_text())["spapi_mcp"]["access_token"], TOKEN)

    def test_cli_status_and_output_file_work_without_native_tools(self):
        target = self.root / "status.json"
        output = io.StringIO()
        with patch.object(MODULE.urllib.request, "build_opener", return_value=Transport()), contextlib.redirect_stdout(output):
            code = MODULE.main(["--config", str(self.config), "status", "--output", str(target)])
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, target.read_text() + output.getvalue())
        self.assertTrue(json.loads(target.read_text())["credentialsConfigured"])

    def test_protocol_and_rpc_errors_fail_explicitly(self):
        with self.assertRaisesRegex(MODULE.ClientError, "MCP_RPC_ERROR"):
            MODULE.parse_response(b'{"jsonrpc":"2.0","id":1,"error":{"code":-1,"message":"ignored"}}', 1, "application/json")
        with self.assertRaisesRegex(MODULE.ClientError, "MCP_RESPONSE_INVALID"):
            MODULE.parse_response(b'not JSON', 1, "application/json")

    def test_collection_checkpoints_pages_and_resumes_without_repeating_the_first_page(self):
        target = self.root / "pages.json"
        first = query_page(next_cursor="two")
        pending_results = iter([first, MODULE.ClientError("MCP_NETWORK_ERROR")])
        class Client:
            def __init__(self, results):
                self.results, self.arguments = iter(results), []
            def call(self, name, arguments):
                self.arguments.append(arguments)
                value = next(self.results)
                if isinstance(value, Exception):
                    raise value
                return value
        with patch.object(MODULE.time, "sleep"):
            with self.assertRaises(MODULE.ClientError):
                MODULE.collect_query(Client(pending_results), QUERY, "WEEK", target)
            self.assertEqual(len(json.loads(target.read_text())["queries"][0]["pages"]), 1)
            resumed = Client([query_page("two", "three", "水筒 子供"), query_page("three", None, "水筒 保冷")])
            result = MODULE.collect_query(resumed, QUERY, "WEEK", target)
        self.assertEqual(resumed.arguments[0]["cursor"], "two")
        self.assertEqual(result["rowsReturned"], 3)
        self.assertTrue(result["coverageComplete"])
        self.assertTrue(result["paginationComplete"])

    def test_running_and_partial_results_are_not_zero_match_success(self):
        class Client:
            def __init__(self, page): self.page = page
            def call(self, *_args): return self.page
        pending = query_page(status="IN_PROGRESS", rows=[], rowsMatched=0, coverageComplete=False)
        result = MODULE.collect_query(Client(pending), QUERY, "WEEK", self.root / "pending.json")
        self.assertEqual(result["status"], "in_progress")
        self.assertFalse(result["coverageComplete"])
        partial = query_page(status="PARTIAL", rowsMatched=1, coverageComplete=False)
        result = MODULE.collect_query(Client(partial), QUERY, "WEEK", self.root / "partial.json")
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["coverageComplete"])


if __name__ == "__main__":
    unittest.main()
