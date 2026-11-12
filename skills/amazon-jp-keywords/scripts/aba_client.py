#!/usr/bin/env python3
"""Call the ABA HTTP MCP directly using the skill config, without Agent MCP registration."""

import argparse
from datetime import date, datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.request

from aba_to_table import convert
from environment import write_json
from skill_config import STATE, aba_settings, read_config

PROTOCOL = "2025-03-26"
MAX_RESPONSE = 8 * 1024 * 1024
TOOL_FIELDS = {
    "aba_status": set(),
    "aba_create_report": {"reportType", "reportPeriod", "dataStartTime", "dataEndTime", "asins"},
    "aba_get_report": {"reportId"},
    "aba_download_report": {"reportId"},
    "aba_read_artifact": {"id", "offset", "length"},
    "aba_query_search_terms": {"reportId", "asins", "seedTerms"},
    "aba_get_search_terms_query": {"queryId", "cursor", "pageSize"},
}


def rolling_window_plan(as_of=None):
    """Plan the seven days preceding the query date as seven valid DAY requests."""
    try:
        query_date = date.fromisoformat(as_of) if as_of is not None else datetime.now(timezone(timedelta(hours=8))).date()
    except (ValueError, TypeError):
        raise ClientError("WINDOW_QUERY_DATE_INVALID") from None
    start = query_date - timedelta(days=7)
    end = query_date - timedelta(days=1)
    reports = []
    for offset in range(7):
        selected = (start + timedelta(days=offset)).isoformat()
        reports.append({"date": selected, "availability": "not_checked", "request": {
            "reportType": "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT", "reportPeriod": "DAY",
            "dataStartTime": selected + "T00:00:00Z", "dataEndTime": selected + "T23:59:59Z"}})
    return {"mode": "ROLLING_7_DAYS", "queryDate": query_date.isoformat(),
            "queryTimezone": "Asia/Shanghai", "marketplaceId": "A1VC38T7YXB528",
            "requestedStartDate": start.isoformat(), "requestedEndDate": end.isoformat(),
            "reportCount": len(reports), "reports": reports,
            "metricAggregation": "retain_daily_metrics_and_union_keywords"}


class ClientError(Exception):
    def __init__(self, code, *, http_status=None, retry_after=None, outcome_unknown=False):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.retry_after = retry_after
        self.outcome_unknown = outcome_unknown


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def parse_response(raw, request_id, content_type):
    try:
        text = raw.decode("utf-8-sig")
        if "text/event-stream" not in content_type:
            messages = [json.loads(text)]
        else:
            messages = []
            lines = []
            for line in [*text.splitlines(), ""]:
                if line.startswith("data:"):
                    lines.append(line[5:].lstrip(" "))
                elif not line and lines:
                    messages.append(json.loads("\n".join(lines)))
                    lines = []
        for message in messages:
            if isinstance(message, dict) and message.get("id") == request_id:
                if "error" in message:
                    raise ClientError("MCP_RPC_ERROR")
                if isinstance(message.get("result"), dict):
                    return message["result"]
    except (UnicodeError, ValueError, TypeError):
        pass
    raise ClientError("MCP_RESPONSE_INVALID")


class AbaClient:
    def __init__(self, config_file=STATE / "config.json", *, timeout=180, opener=None):
        self.config_file = Path(config_file).expanduser().resolve()
        try:
            settings = aba_settings(read_config(Path(config_file).expanduser()))
        except FileNotFoundError:
            raise ClientError("CLIENT_CONFIG_MISSING") from None
        except ValueError as error:
            code = str(error)
            if code not in {"ABA_MCP_CONFIG_CONFLICT", "ABA_MCP_CONFIG_INVALID", "ABA_MCP_URL_INVALID",
                            "CONFIG_SYMLINK_REFUSED", "CONFIG_OBJECT_REQUIRED", "INVALID_TOKEN"}:
                code = "CLIENT_CONFIG_INVALID"
            raise ClientError(code) from None
        if settings["access_token"] is None:
            raise ClientError("ABA_MCP_ACCESS_TOKEN_MISSING")
        self.url = settings["url"]
        self.token = settings["access_token"]
        self.config_source = settings["source"]
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener(NoRedirect)
        self.request_id = 0
        self.session = None
        self.initialized = False

    def _post(self, method, params=None, *, notification=False):
        self.request_id += 1
        body = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = self.request_id
        if params is not None:
            body["params"] = params
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.initialized:
            headers["MCP-Protocol-Version"] = PROTOCOL
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"),
                                         headers=headers, method="POST")
        creating = method == "tools/call" and (params or {}).get("name") == "aba_create_report"
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ClientError("MCP_RESPONSE_TOO_LARGE", outcome_unknown=creating)
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    if len(session) > 1024 or not session.isascii() or any(ord(c) < 33 for c in session):
                        raise ClientError("MCP_SESSION_INVALID")
                    self.session = session
                if notification and response.status in (202, 204) and not raw:
                    return None
                return parse_response(raw, body.get("id"), response.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as error:
            retry = error.headers.get("Retry-After", "") if error.headers else ""
            error.close()
            raise ClientError("MCP_HTTP_ERROR", http_status=error.code,
                              retry_after=int(retry) if retry.isdigit() else None,
                              outcome_unknown=creating and error.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, http.client.HTTPException):
            raise ClientError("MCP_NETWORK_ERROR", outcome_unknown=creating) from None

    def initialize(self):
        if self.initialized:
            return
        result = self._post("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                            "clientInfo": {"name": "amazon-jp-keywords-cli", "version": "0.1.0"}})
        if result.get("protocolVersion") != PROTOCOL:
            raise ClientError("MCP_PROTOCOL_UNSUPPORTED")
        self.initialized = True
        self._post("notifications/initialized", notification=True)

    def list_tools(self):
        self.initialize()
        return self._post("tools/list", {})

    def call(self, name, arguments=None):
        if name not in TOOL_FIELDS or not isinstance(arguments or {}, dict):
            raise ClientError("MCP_ARGUMENTS_INVALID")
        arguments = {} if arguments is None else arguments
        if not isinstance(arguments, dict) or set(arguments) - TOOL_FIELDS[name]:
            raise ClientError("MCP_ARGUMENTS_INVALID")
        self.initialize()
        try:
            result = self._post("tools/call", {"name": name, "arguments": arguments})
        except ClientError as error:
            if name == "aba_create_report" and error.code == "MCP_RESPONSE_INVALID":
                error.outcome_unknown = True
            raise
        try:
            if isinstance(result.get("structuredContent"), dict):
                data = result["structuredContent"]
            else:
                data = json.loads("\n".join(block["text"] for block in result.get("content", [])
                                            if block.get("type") == "text"))
        except (ValueError, KeyError, TypeError):
            raise ClientError("MCP_TOOL_RESPONSE_INVALID", outcome_unknown=name == "aba_create_report") from None
        if not isinstance(data, dict):
            raise ClientError("MCP_TOOL_RESPONSE_INVALID")
        if result.get("isError"):
            code = data.get("error", "MCP_TOOL_FAILED")
            if not isinstance(code, str) or not code or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in code):
                code = "MCP_TOOL_FAILED"
            raise ClientError(code)
        return data


def protect_output(path, protected):
    if path is None:
        return
    if path.suffix.lower() != ".json" or path.resolve() in {item.resolve() for item in protected}:
        raise ClientError("OUTPUT_PATH_INVALID")


def collect_query(client, query_id, period, output):
    """Save all result pages; resume an interrupted collection from its saved cursor."""
    pages = []
    if output.exists():
        try:
            document = json.loads(output.read_text(encoding="utf-8"))
            queries = document["queries"]
            if len(queries) != 1 or queries[0]["reportPeriod"] != period:
                raise ValueError()
            convert(document)
            previous = queries[0]["pages"]
            if previous[0]["queryId"] != query_id:
                raise ValueError()
            if previous[0]["status"] in ("DONE", "PARTIAL"):
                pages = previous
        except (ValueError, KeyError, TypeError, IndexError):
            raise ClientError("EXISTING_QUERY_PAGES_INVALID") from None
    cursor = pages[-1].get("nextCursor") if pages else None
    seen = {page.get("cursor") for page in pages}
    for _page_number in range(10000):
        if pages and cursor is None:
            break
        arguments = {"queryId": query_id, "pageSize": 100}
        if cursor:
            arguments["cursor"] = cursor
        page = client.call("aba_get_search_terms_query", arguments)
        if page.get("queryId") != query_id:
            raise ClientError("MCP_QUERY_ID_MISMATCH")
        if page.get("status") not in ("DONE", "PARTIAL"):
            if pages:
                raise ClientError("MCP_QUERY_STATE_CHANGED")
            pages = [page]
        else:
            if page.get("cursor") != cursor:
                raise ClientError("MCP_QUERY_CURSOR_INVALID")
            if page.get("nextCursor") is not None and (page["nextCursor"] in seen or page["nextCursor"] == cursor):
                raise ClientError("MCP_QUERY_CURSOR_REPEATED")
            seen.add(cursor)
            pages.append(page)
        document = {"queries": [{"reportPeriod": period, "pages": pages}]}
        convert(document)
        write_json(output, document)
        cursor = page.get("nextCursor")
        if cursor is None or page.get("status") not in ("DONE", "PARTIAL"):
            break
        time.sleep(0.5)
    else:
        raise ClientError("QUERY_PAGE_LIMIT")
    status = convert({"queries": [{"reportPeriod": period, "pages": pages}]})["query_statuses"][0]
    result = {"status": status["status"], "queryId": query_id, "output_file": str(output),
            "pages": len(pages), **{key: status[key] for key in
            ("coverageComplete", "paginationComplete", "rowsReturned", "rowsMatched")}}
    for source, target in (("error", "error_code"), ("truncationReason", "truncation_reason")):
        value = status.get(source)
        if isinstance(value, str) and value and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in value):
            result[target] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=STATE / "config.json")
    parser.add_argument("--timeout", type=float, default=180)
    commands = parser.add_subparsers(dest="command")
    window = commands.add_parser("plan-window")
    window.add_argument("--as-of", help="Query date YYYY-MM-DD; defaults to today's Asia/Shanghai date")
    window.add_argument("--output", type=Path)
    for name in ("status", "tools"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path)
    call = commands.add_parser("call")
    call.add_argument("--tool", choices=sorted(TOOL_FIELDS), required=True)
    call.add_argument("--arguments-file", type=str)
    call.add_argument("--output", type=Path)
    collect = commands.add_parser("collect-query")
    collect.add_argument("--query-id", required=True)
    collect.add_argument("--report-period", choices=("DAY", "WEEK", "MONTH", "QUARTER"), required=True)
    collect.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    client = None
    try:
        if not 0 < args.timeout <= 300:
            raise ClientError("TIMEOUT_INVALID")
        output = getattr(args, "output", None)
        protected = [args.config.expanduser(), Path(__file__)]
        arguments = {}
        if args.command == "call" and args.arguments_file:
            if args.arguments_file == "-":
                arguments = json.load(sys.stdin)
            else:
                source = Path(args.arguments_file).expanduser()
                if source.resolve() == args.config.expanduser().resolve():
                    raise ClientError("ARGUMENT_FILE_IS_CONFIG")
                protected.append(source)
                arguments = json.loads(source.read_text(encoding="utf-8-sig"))
        protect_output(output, protected)
        if args.command == "plan-window":
            data = rolling_window_plan(args.as_of)
            if output:
                write_json(output, data)
                data = {key: data[key] for key in ("mode", "queryDate", "requestedStartDate", "requestedEndDate", "reportCount")}
                data["output_file"] = str(output)
            print(json.dumps(data, ensure_ascii=False))
            return 0
        client = AbaClient(args.config, timeout=args.timeout)
        if args.command == "collect-query":
            result = collect_query(client, args.query_id, args.report_period, output)
            print(json.dumps(result, ensure_ascii=False))
            return 1 if result["status"] == "failed" else 0
        tool = args.tool if args.command == "call" else "aba_status"
        data = client.list_tools() if args.command == "tools" else client.call(tool, arguments)
        if output:
            write_json(output, data)
            data = {"status": "completed", "output_file": str(output),
                    **{key: data[key] for key in ("reportId", "queryId") if key in data},
                    "remote_status": data.get("status", data.get("processingStatus"))}
        print(json.dumps(data, ensure_ascii=False))
        return 0
    except ClientError as error:
        result = {"status": "failed", "error_code": error.code}
        if error.http_status is not None:
            result["http_status"] = error.http_status
        if error.retry_after is not None:
            result["retry_after_seconds"] = error.retry_after
        if error.outcome_unknown:
            result["request_outcome_unknown"] = True
        print(json.dumps(result))
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print('{"status":"failed","error_code":"CLIENT_CONFIG_OR_DATA_INVALID"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
