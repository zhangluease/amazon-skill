#!/usr/bin/env python3
"""Query the first three Japanese SellerSprite keyword-miner pages over HTTP."""

import argparse
from datetime import datetime, timezone
from http.client import HTTPException
import json
from pathlib import Path
import shlex
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from environment import CACHE, SetupError, write_json
from export_xlsx import write_xlsx
from sellersprite_login import LoginError, ORIGIN, check_browser, login_and_fetch


ENDPOINT = ORIGIN + "/v3/api/keyword-miner"
SESSION_FILE = CACHE / "sellersprite-keyword-session.json"
SESSION_ERRORS = {"HTTP_401", "HTTP_403", "SELLERSPRITE_SESSION_EXPIRED"}
SOURCE = "卖家精灵关键词挖掘"
PAGE_SIZE = 50
HEADERS = [
    "日文关键词", "种子词", "来源", "数据月份", "来源页码",
    "相关度", "月搜索量", "月购买量", "购买率", "展示量", "点击量",
    "商品数", "广告竞品数", "SPR", "标题密度", "需供比",
    "PPC精准竞价JPY", "PPC精准最低价JPY", "PPC精准最高价JPY",
    "卖家精灵ABA月排名", "点击集中度", "转化集中度",
]
FIELDS = [
    "relevancy", "searches", "purchases", "purchaseRate", "impressions", "clicks",
    "products", "adProducts", "spr", "titleDensity", "supplyDemandRatio",
    "exactPpc", "minExactPpc", "maxExactPpc", "searchRank", "monopolyClickRate", "cvsShareRate",
]


class QueryError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def import_session(curl_file):
    """Read a copied request as data, never as a shell command."""
    text = curl_file.read_text(encoding="utf-8-sig").replace("\\\n", "")
    args = shlex.split(text)
    if not args or args.pop(0) != "curl":
        raise QueryError("INVALID_CURL_CAPTURE")
    url = None
    headers = {}
    body = None
    index = 0
    value_options = {"--url", "-H", "--header", "-b", "--cookie",
                     "--data-raw", "--data", "--data-binary", "-X", "--request"}
    while index < len(args):
        option = args[index]
        if option == "--compressed":
            index += 1
            continue
        if option.startswith("https://"):
            url = option
            index += 1
            continue
        if option not in value_options or index + 1 >= len(args):
            raise QueryError("UNSUPPORTED_CURL_CAPTURE")
        value = args[index + 1]
        index += 2
        if option == "--url":
            url = value
        elif option in {"-b", "--cookie"}:
            headers["Cookie"] = value
        elif option in {"-H", "--header"}:
            name, separator, content = value.partition(":")
            if separator and name.lower() in {"cookie", "user-agent"}:
                headers[{"cookie": "Cookie", "user-agent": "User-Agent"}[name.lower()]] = content.strip()
        elif option in {"-X", "--request"}:
            if value != "POST":
                raise QueryError("INVALID_CAPTURE_METHOD")
        else:
            body = json.loads(value)
    parsed = urlsplit(url or "")
    if (parsed.scheme != "https" or parsed.netloc != "www.sellersprite.com"
            or parsed.path != "/v3/api/keyword-miner" or parsed.query or parsed.fragment):
        raise QueryError("INVALID_CAPTURE_ENDPOINT")
    if not isinstance(body, dict) or body.get("market") != 6 or not isinstance(body.get("keyword"), str):
        raise QueryError("JAPAN_KEYWORD_CAPTURE_REQUIRED")
    if not headers.get("Cookie") or any("\r" in value or "\n" in value for value in headers.values()):
        raise QueryError("SESSION_COOKIE_MISSING_OR_INVALID")
    write_json(SESSION_FILE, headers)


def load_session():
    try:
        session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        if (not isinstance(session, dict) or set(session) - {"Cookie", "User-Agent"}
                or not isinstance(session.get("Cookie"), str) or not session["Cookie"]
                or any(not isinstance(value, str) or "\r" in value or "\n" in value
                       for value in session.values())):
            raise ValueError
        return session
    except (OSError, ValueError):
        raise QueryError("SELLERSPRITE_SESSION_REQUIRED") from None


def request_page(seed, page, session):
    parameters = {
        "keyword": seed, "market": 6, "pageNum": page, "pageSize": PAGE_SIZE,
        "historyDate": "", "orderBy": 21, "desc": True, "filterRootWord": 0,
        "matchType": 0, "amazonChoice": False, "keywordBidMatchType": "exact",
    }
    headers = {
        **session, "Content-Type": "application/json", "Accept": "application/json",
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/v3/keyword-miner/",
    }
    request = Request(ENDPOINT, data=json.dumps(parameters).encode("utf-8"), headers=headers, method="POST")
    try:
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        status = error.code
        location = error.headers.get("Location") if error.headers else None
        try:
            if 300 <= status < 400 and location:
                target = urlsplit(urljoin(ENDPOINT, location))
                if (target.scheme == "https" and target.netloc == "www.sellersprite.com"
                        and target.path.rstrip("/").endswith(("/w/user/login", "/w/user/signin"))):
                    raise QueryError("SELLERSPRITE_SESSION_EXPIRED") from None
        finally:
            error.close()
        raise QueryError(f"HTTP_{status}") from None
    except (URLError, OSError, HTTPException):
        raise QueryError("NETWORK_ERROR") from None
    except ValueError:
        raise QueryError("INVALID_RESPONSE") from None
    if not isinstance(payload, dict) or payload.get("success") is not True or payload.get("code") != "OK":
        if isinstance(payload, dict) and str(payload.get("code")).upper() in {
                "401", "NOT_LOGIN", "LOGIN_REQUIRED", "SESSION_EXPIRED", "UNAUTHORIZED"}:
            raise QueryError("SELLERSPRITE_SESSION_EXPIRED")
        raise QueryError("SELLERSPRITE_REJECTED_REQUEST")
    data = payload.get("data")
    if (not isinstance(data, dict) or not isinstance(data.get("items"), list)
            or type(data.get("total")) is not int or data["total"] < 0
            or data.get("page") != page or data.get("size") != PAGE_SIZE
            or len(data["items"]) > PAGE_SIZE):
        raise QueryError("INVALID_PAGE")
    for item in data["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("keyword"), str) or not item["keyword"].strip():
            raise QueryError("INVALID_KEYWORD")
    return data


def recover_session(seed, page):
    def fetch(session):
        try:
            return request_page(seed, page, session)
        except QueryError as error:
            if str(error) not in SESSION_ERRORS:
                raise
            return None

    try:
        session, data = login_and_fetch(ENDPOINT, fetch)
    except LoginError as error:
        raise QueryError(str(error)) from None
    try:
        write_json(SESSION_FILE, session)
    except OSError:
        raise QueryError("SELLERSPRITE_SESSION_SAVE_FAILED") from None
    return session, data


def collect(seeds, page_limit, output):
    if page_limit not in (1, 2, 3):
        raise QueryError("PAGE_LIMIT_MUST_BE_1_TO_3")
    try:
        session = load_session()
    except QueryError:
        session = None
    login_attempted = False
    records = []
    queries = []
    stopped = False
    for seed in dict.fromkeys(seeds):
        query = {"seed": seed, "status": "skipped", "pages": []}
        queries.append(query)
        if stopped:
            continue
        query.update(status="ok", total=None, page_limit=page_limit)
        seen = set()
        for page in range(1, page_limit + 1):
            if records:
                time.sleep(1)
            try:
                if session is not None:
                    try:
                        data = request_page(seed, page, session)
                    except QueryError as error:
                        if login_attempted or str(error) not in SESSION_ERRORS:
                            raise
                        session = None
                if session is None:
                    login_attempted = True
                    session, data = recover_session(seed, page)
                keywords = [item["keyword"].strip() for item in data["items"]]
                if any(keyword in seen for keyword in keywords) or len(set(keywords)) != len(keywords):
                    raise QueryError("PAGE_OVERLAP")
                if not keywords and (page - 1) * PAGE_SIZE < data["total"]:
                    raise QueryError("UNEXPECTED_EMPTY_PAGE")
            except QueryError as error:
                query.update(status="partial" if query["pages"] else "failed", error_code=str(error))
                stopped = True
                break
            seen.update(keywords)
            query["total"] = data["total"]
            query["pages"].append({"page": page, "count": len(data["items"]), "total": data["total"]})
            for item in data["items"]:
                records.append({"seed": seed, "page": page, "item": item})
            if page * PAGE_SIZE >= data["total"]:
                break
        query["collected"] = len(seen)
        query["has_more"] = query["total"] is not None and len(seen) < query["total"]

    rows = [[
        record["item"]["keyword"], record["seed"], SOURCE,
        record["item"].get("month"), record["page"],
        *[record["item"].get(field) for field in FIELDS],
    ] for record in records]
    status = "partial" if stopped and records else "failed" if stopped else "ok"
    table = {
        "status": status, "source": SOURCE, "marketplace": "JP",
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "queries": queries, "records": records,
        "headers": HEADERS, "rows": rows,
        "percentage_columns": ["购买率", "点击集中度", "转化集中度"],
    }
    write_json(output / "sellersprite-keywords.json", table)
    try:
        write_xlsx(output / "sellersprite-keywords.xlsx", HEADERS, rows, table["percentage_columns"])
        export_status = "completed"
    except (SetupError, OSError, ValueError):
        export_status = "failed"
    return {"status": status, "count": len(rows), "queries": queries, "export_status": export_status}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("import-session", help="Import one Japan keyword-miner request copied as cURL")
    setup.add_argument("--curl-file", required=True, type=Path)
    check = commands.add_parser("browser-check", help="Verify a visible browser opens without signing in")
    check.add_argument("--prepare", action="store_true", help="Install missing browser dependencies before checking")
    search = commands.add_parser("search", help="Collect up to 3 pages per seed")
    search.add_argument("seeds", nargs="+")
    search.add_argument("--pages", type=int, choices=(1, 2, 3), default=3)
    search.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "search" and any(not seed.strip() for seed in args.seeds):
        parser.error("Each seed must contain text.")
    try:
        if args.command == "import-session":
            import_session(args.curl_file)
            result = {"status": "completed"}
        elif args.command == "browser-check":
            print(json.dumps(check_browser(prepare=args.prepare)))
            return 0
        else:
            result = collect(args.seeds, args.pages, args.out.resolve())
    except (QueryError, LoginError, OSError, ValueError) as error:
        code = str(error) if isinstance(error, (QueryError, LoginError)) else "LOCAL_INPUT_OR_OUTPUT_ERROR"
        print(json.dumps({"status": "failed", "error_code": code}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"ok", "completed"} and result.get("export_status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
