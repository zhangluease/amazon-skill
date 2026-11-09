#!/usr/bin/env python3
"""Read J-PlatPat search results, public gazettes, and case status without a browser."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


BASE_URL = "https://www.j-platpat.inpit.go.jp"
JST = timezone(timedelta(hours=9))
SESSION_ENDPOINT = "/app/docgw/wsc1101"
SEARCH_ENDPOINTS = {
    "patent": "/app/comsrch/wsp0103",
    "design": "/app/dtaapi/wsd0203",
}
SEARCH_FILTERS = {
    "patent": {"FILTER_MAXIMUM_CNT_KNOWN_Y_BY": 10, "FILTER_MAXIMUM_CNT_PER_FI": 10},
    "design": {"FILTER_MAXIMUM_CNT_PER_APP_Y_BY": 10, "FILTER_MAXIMUM_CNT_PER_CLASSIFY": 10},
}
DOC_INFO = "/app/comdocu/wsp1101"
CLAIMS = "/app/comdocu/wsp1203"
DESIGN_INFO = "/app/dtaapi/wsd0601"
CASE_INFO = "/app/docgw/wsh0101"
CASE_TYPES = {"patent": ("特願", "1", "PU", "10"), "design": ("意匠出願", "3", "DE", "30")}
PATENT_STAGES = {
    "10": "審査請求前",
    "21": "審査中",
    "22": "査定不服",
    "40": "特許 有効",
    "50": "異議申立中・無効審判中",
    "61": "（出願の）却下・拒絶",
    "62": "特許 消滅",
}
PATENT_KEY = re.compile(r"(?:特許[0-9]{1,8}|特[開公](?:[0-9]{4}|[明大昭平令][0-9]{1,2})-[0-9]{1,6})")
RESULT_LIMIT = 500
JSON_LIMIT = 8 * 1024 * 1024
PDF_LIMIT = 20 * 1024 * 1024


class QueryError(Exception):
    def __init__(self, message, status="failed"):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def result_info(data, code_field="RSLT_CD"):
    info = data.get("RSLT_INFO")
    if not isinstance(info, dict) or type(info.get(code_field)) is not int:
        raise QueryError(f"Missing J-PlatPat business result: {code_field}")
    return info, info[code_field]


def require_success(data, code_field="RSLT_CD"):
    info, code = result_info(data, code_field)
    if code != 0:
        raise QueryError(
            f"J-PlatPat returned {code_field}={code}, message={info.get('MSG_ID')}",
            "verification_required" if code == 8 else "failed",
        )


class Client:
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.opener = build_opener(NoRedirect())
        self.session_id = str(uuid4())
        self.last_request = None
        self.trace = []

    def request(self, endpoint, *, payload=None, cookies=None, limit=JSON_LIMIT):
        url = BASE_URL + endpoint
        headers = {"Accept": "application/pdf" if payload is None else "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
        entry = {"url": url, "method": "GET" if body is None else "POST"}
        self.trace.append(entry)
        if self.last_request is not None:
            time.sleep(max(0, 1 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            with self.opener.open(Request(url, data=body, headers=headers), timeout=25) as response:
                entry["http_status"] = response.status
                entry["content_type"] = response.headers.get_content_type()
                data = response.read(limit + 1)
        except HTTPError as exc:
            entry["http_status"] = exc.code
            exc.close()
            status = "access_limited" if exc.code in {401, 403, 429, 503} else "failed"
            raise QueryError(f"J-PlatPat HTTP {exc.code}: {endpoint}", status) from None
        except (URLError, OSError) as exc:
            raise QueryError(f"J-PlatPat request failed: {exc}") from None
        finally:
            entry["seconds"] = round(time.monotonic() - self.last_request, 3)
        if len(data) > limit:
            raise QueryError(f"Response exceeded the byte limit: {endpoint}")
        return data

    def post(self, endpoint, payload, name=None, cookies=None):
        if name:
            save_json(self.output_dir / f"{name}-request.json", payload)
        raw = self.request(endpoint, payload=payload, cookies=cookies)
        if name:
            (self.output_dir / f"{name}-response.json").write_bytes(raw)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            raise QueryError(f"Expected a JSON response: {endpoint}") from None
        if not isinstance(data, dict):
            raise QueryError(f"Expected a JSON object: {endpoint}")
        return data

    def anonymous_cookies(self, screen):
        return {
            "U": self.session_id,
            "T": datetime.now(JST).strftime("%Y%m%d%H%M%S%f")[:17],
            "L": "ja",
            "D": screen,
            "S": "www",
        }

    def anonymous_session(self, endpoint, screen, document_key=""):
        # Follow the site's anonymous request initialization. The challenge
        # response and cookies are never saved with the search evidence.
        cookies = self.anonymous_cookies(screen)
        service_id = endpoint.rsplit("/", 1)[-1].upper()
        data = self.post(SESSION_ENDPOINT, {"SERVICE_ID": service_id}, cookies=cookies)
        require_success(data)
        flags, key = data.get("ECRL"), data.get("ECKY")
        if type(flags) not in {int, str} or not re.fullmatch(r"[0-9]{1,3}", str(flags)):
            raise QueryError("Unexpected J-PlatPat session flags")
        if not 0 < int(flags) < 128 or not isinstance(key, str) or not key:
            raise QueryError("Incomplete J-PlatPat anonymous session response")
        fields = [document_key, cookies["U"], cookies["T"], cookies["L"], screen, cookies["S"], key]
        message = "".join(field for enabled, field in zip(f"{int(flags):07b}", fields) if enabled == "1")
        cookies["Z"] = hashlib.sha256(message.encode("utf-8")).hexdigest()
        return cookies


def search_payload(kind, query, limit):
    payload = {
        "DISP_ID": "S0000",
        "SEARCH_TYPE": "1",
        "SIMPLE_SEARCH_DISP": {
            "AUTO_NARROW_FLG": 0,
            "SEARCH_COND": [{"NUM_TYPE": [], "KEYWD": word} for word in query.split()],
            "SEARCH_OPTN": {
                "FILTER_INFO": {**SEARCH_FILTERS[kind], "ITEM": None, "COND": None, "COMPAR_COND_SIGN": None}
            },
        },
        "SEARCH_RSLT_MAX_CNT": limit,
        "NUM_LST_MAX_CNT": 0 if kind == "patent" else limit,
        "INPUT_NUM_LST": [],
        "SORT_INFO": {"ITEM": None, "SORT_ORDER": 0},
    }
    if kind == "patent":
        payload["OTID"] = None
    return payload


def application_digits(kind, value):
    if not isinstance(value, str):
        return None
    prefix = CASE_TYPES[kind][0]
    value = unicodedata.normalize("NFKC", value.strip())
    match = re.fullmatch(rf"(?:{prefix})?([0-9]{{4}})-?([0-9]{{6}})", value)
    return "".join(match.groups()) if match else None


def case_reference(kind, digits):
    if not digits or (kind == "design" and digits[4] == "5"):
        return {}
    prefix, _, category, url_type = CASE_TYPES[kind]
    number = f"{digits[:4]}-{digits[4:]}"
    return {
        "application_number": prefix + number,
        "case_url": f"{BASE_URL}/c1801/{category}/JP-{number}/{url_type}/ja",
    }


def candidate(kind, row):
    common = {
        "application_number": row.get("APP_NUM_DISP") or row.get("APP_NUM"),
        "registration_number": row.get("REG_NUM_DISP") or row.get("REG_NUM"),
        "applicants_or_holders": row.get("APPN_RIGHT_HOLDER"),
        "filing_date": row.get("APP_DATE"),
    }
    reference = case_reference(kind, application_digits(kind, common["application_number"]))
    if reference:
        common["case_url"] = reference["case_url"]
    if kind == "patent":
        return {
            **common,
            "title": row.get("INVEN_NAME"),
            "publication_number": row.get("PUBLI_NUM_DISP"),
            "gazette_keys": [
                row[field + "_DISP"] for field in ("REG_NUM", "PUBLI_NUM", "EXAM_PUB_NUM")
                if row.get(field + "_DOCU_EXISTS_FLG") == 1
                and PATENT_KEY.fullmatch(row.get(field + "_DISP") or "")
            ],
            "fi": row.get("FI"),
            "status_code": row.get("STATUS"),
        }
    return {
        **common,
        "article": row.get("DESIGN_GOODS"),
        "document_id": row.get("DOCU_NUM"),
        "gazette_type": row.get("BUL_TYPE"),
        "publication_date": row.get("BUL_PUBL_DATE"),
        "classification": row.get("JPN_DESIGN_CLASSIFY_OLD_JPN_DESIGN_CLASSIFY"),
        "partial_design_flag": row.get("PART_DESIGN"),
        "related_design_registration": row.get("RLT_SMLR_SEARCH_REG_NUM"),
    }


def summarize_search(kind, data):
    info, code = result_info(data)
    hit_count = info.get("SEARCH_HIT_CNT")
    rows = data.get("SEARCH_RSLT_LIST")
    if type(hit_count) is not int or hit_count < 0:
        if code == 0:
            raise QueryError("Invalid SEARCH_HIT_CNT")
        hit_count = None
    if (code == 0 and "SEARCH_RSLT_LIST" not in data) or (rows is not None and not isinstance(rows, list)):
        raise QueryError("Invalid SEARCH_RSLT_LIST")
    rows = rows or []
    if any(not isinstance(row, dict) for row in rows):
        raise QueryError("Invalid search result row")
    candidates = []
    ignored = unknown = 0
    for index, row in enumerate(rows):
        if kind == "patent" and row.get("LAW_DIVISION") != "P":
            if row.get("LAW_DIVISION") == "U":
                ignored += 1
            else:
                unknown += 1
            continue
        candidates.append({"source_row_index": index, **candidate(kind, row)})
    narrowing = info.get("AUTO_NARROW_EXEC")
    narrowed = isinstance(narrowing, dict) and bool(narrowing.get("AUTO_NARROW_PREV_COUNT_CNT"))
    complete = code == 0 and len(rows) == hit_count and not narrowed and unknown == 0
    message_id = info.get("MSG_ID")
    if code == 8:
        status = "verification_required"
    elif code != 0 and message_id in {"C90003-E", "C10102-E"}:
        status = "needs_refinement"
    elif code != 0:
        status = "failed"
    elif not complete:
        status = "partial"
    else:
        status = "ok" if candidates else "no_result"
    return {
        "status": status,
        "business_code": code,
        "message_id": message_id,
        "server_hits": hit_count,
        "rows_returned": len(rows),
        "candidates_in_scope": len(candidates),
        "excluded_utility_models": ignored,
        "unclassified_patent_rows": unknown,
        "server_auto_narrowing": narrowing,
        "complete_within_query": complete,
        "candidates": candidates,
    }


def single_record(data, field):
    records = data.get(field)
    if isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict):
        return records[0]
    return {}


def summarize_case_status(kind, digits, data):
    info = data.get("status")
    if not isinstance(info, dict) or type(info.get("resultCode")) is not int:
        raise QueryError("Missing J-PlatPat case business result")
    code = info["resultCode"]
    if code != 0:
        raise QueryError(
            f"J-PlatPat case resultCode={code}, message={info.get('messageId')}",
            "verification_required" if code == 8 else "failed",
        )
    row = single_record(data, "responseData")
    application = single_record(single_record(row, "app_info"), "app_art")
    if application.get("law_cd") != CASE_TYPES[kind][1] or application.get("app_num") != digits:
        raise QueryError("Case identity is missing, ambiguous, or does not match the requested application")

    registration = single_record(row, "reg_info")
    legal = row.get("status")
    stage = legal.get("stage") if isinstance(legal, dict) else None
    event = legal.get("detailed_event") if isinstance(legal, dict) else None
    label = PATENT_STAGES.get(stage) if kind == "patent" and isinstance(stage, str) else None
    return {
        "status": "ok" if label else "partial",
        **case_reference(kind, digits),
        "registration_number": single_record(registration, "reg_art").get("reg_num"),
        "stage_code": stage,
        "stage_label": label,
        "detailed_event_code": event,
        "detailed_event_label": "年金納付" if label and event == "U110" else None,
        "current_legal_status_verified": label is not None,
    }


def case_status(client, kind, digits):
    if not case_reference(kind, digits):
        raise QueryError("This design application uses a different case route", "material_unavailable")
    law = CASE_TYPES[kind][1]
    payload = {
        "displayId": "H0000", "searchCondition": {"lawCd": law, "applicationNumber": digits}, "OTID": None,
    }
    cookies = client.anonymous_session(CASE_INFO, "H0000", f"{law}-{digits}")
    return summarize_case_status(kind, digits, client.post(CASE_INFO, payload, "case", cookies))


class ClaimsText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "claim", "claim-text"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "claim", "claim-text"}:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def patent_claims(client, key):
    doc = client.post(
        DOC_INFO, {"DOCU_KEY": key, "JPN_TEXT_ENG_TEXT_FLG": 0, "ISN": None, "LANG": "ja", "OTID": None},
        "document", client.anonymous_session(DOC_INFO, "P0201", key),
    )
    require_success(doc, "STATUS_CD_A")
    if unicodedata.normalize("NFKC", str(doc.get("DOCU_NAME") or "")) != key:
        raise QueryError("Document identity does not match the requested patent gazette")
    flags = doc.get("DOCU_EXISTS_FLG_INFO")
    if not isinstance(flags, dict) or flags.get("CLAIM_RNG_AREA_DATA_EXISTS_FLG") != 1:
        raise QueryError("Claims are unavailable as text for this gazette", "material_unavailable")
    isn = doc.get("ISN_NO")
    if not isinstance(isn, str) or not re.fullmatch(r"[0-9]+", isn):
        raise QueryError("Missing patent document ISN")
    payload = {
        "DOCU_KEY": key, "ACQUISITION_MODE": "0", "SPC_NUM": 3, "TOTAL_PAGE_CNT": 0,
        "USE_OF_LANG": "ja", "WABUN_EIBUN": "0", "BLOCK_NUM": 0, "ISN": isn, "OTID": None,
    }
    data = client.post(CLAIMS, payload, "claims", client.anonymous_session(CLAIMS, "P0201", key))
    require_success(data, "STATUS_CD_A")
    area = data.get("DOCU_DATA")
    text = area.get("TEXT_DATA") if isinstance(area, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise QueryError("Claims response has no readable text", "material_unavailable")
    parser = ClaimsText()
    parser.feed(text)
    parser.close()
    extracted = "".join(parser.parts).strip()
    if not extracted:
        raise QueryError("Claims response has no readable text", "material_unavailable")
    (client.output_dir / "claims.txt").write_text(extracted + "\n", encoding="utf-8")
    complete = data.get("NEXT_POS_FLG") == 0
    digits = application_digits("patent", doc.get("PATENT_APP_NUM"))
    return {
        "status": "ok" if complete else "partial",
        **case_reference("patent", digits),
        "document": key,
        "claims_file": "claims.txt",
        "document_kind": "grant_gazette" if key.startswith("特許") else "publication_gazette",
        "continuation_flag": data.get("NEXT_POS_FLG"),
        "correction_link_flag": flags.get("AMEND_REV_BUL_LINK_DISP_FLG"),
        "current_legal_status_verified": False,
    }


def design_pdf_path(record, document_id):
    volume = str(record.get("VOLUME_NUM") or "")
    content_hash = str(record.get("HASH_VALUE") or "")
    if (
        record.get("PDF_DISP_FLG") != "1" or record.get("BUL_TYPE") != "DG"
        or not re.fullmatch(r"D_[0-9]{7}", volume) or int(volume[2:]) < 2022002
        or not re.fullmatch(r"[0-9A-Fa-f]{64}", content_hash)
    ):
        raise QueryError("No supported cached PDF for this domestic design gazette", "material_unavailable")
    number = document_id.zfill(10)
    previous = str(int(number) - 1).zfill(10)
    units = "/".join(previous[:-len(tail)] + tail for tail in ("001", "01", "1"))
    return f"/cache/gazette_work/DESIGN_BUL/{volume[2:6]}/{volume[6:]}/DG/{units}/{number}/{content_hash}/{number}.pdf"


def design_pdf(client, document_id):
    data = client.post(
        DESIGN_INFO, {"DISP_ID": "D0202", "DOCU_ID_NUM": document_id},
        "document", client.anonymous_cookies("D0202"),
    )
    require_success(data)
    records = data.get("MGT_INFO_LST")
    if not isinstance(records, list):
        raise QueryError("No management record for this design gazette", "material_unavailable")
    matching = [row for row in records if isinstance(row, dict) and row.get("DOCU_ID_NUM") == document_id]
    if len(matching) != 1:
        raise QueryError("Design gazette identity is missing or ambiguous", "material_unavailable")
    record = matching[0]
    path = design_pdf_path(record, document_id)
    raw = client.request(path, limit=PDF_LIMIT)
    if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-2048:]:
        raise QueryError("Design gazette response was not a PDF", "material_unavailable")
    (client.output_dir / "gazette.pdf").write_bytes(raw)
    return {
        **case_reference("design", application_digits("design", record.get("APP_NUM"))),
        "status": "ok", "document_id": document_id, "pdf_file": "gazette.pdf",
        "pdf_url": BASE_URL + path, "current_legal_status_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    search = commands.add_parser("search", help="search Japanese patent or design candidates")
    search.add_argument("kind", choices=SEARCH_ENDPOINTS)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=100, help=f"result cap (1–{RESULT_LIMIT})")
    case = commands.add_parser("case-status", help="read case information for an exact Japanese application")
    case.add_argument("kind", choices=CASE_TYPES)
    case.add_argument("application_number")
    commands.add_parser("patent-claims", help="read claims for one exact invention gazette").add_argument("key")
    commands.add_parser("design-pdf", help="read one domestic registered design gazette").add_argument("document_id")
    for command in commands.choices.values():
        command.add_argument("--out", required=True, type=Path, help="new directory for request evidence")
    args = parser.parse_args()
    if args.operation == "search":
        args.query = args.query.strip()
        if not args.query or len(args.query) > 120 or len(args.query.split()) > 5 or not 1 <= args.limit <= RESULT_LIMIT:
            parser.error(f"use a specific query (1–120 characters, up to 5 terms) and a limit of 1–{RESULT_LIMIT}")
    elif args.operation == "patent-claims":
        args.key = unicodedata.normalize("NFKC", args.key.strip())
        if not PATENT_KEY.fullmatch(args.key):
            parser.error("use an exact Japanese invention gazette number (特許, 特開, or 特公)")
    elif args.operation == "case-status":
        args.application_number = application_digits(args.kind, args.application_number)
        if not args.application_number:
            parser.error("use a Japanese application number with a four-digit year and six-digit sequence")
    elif not re.fullmatch(r"[0-9]{7}", args.document_id) or args.document_id == "0000000":
        parser.error("use the seven-digit domestic design document_id from a search result")
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        parser.error(f"cannot create evidence directory: {exc}")
    client = Client(args.out)
    started = datetime.now(JST).isoformat(timespec="seconds")
    context = {"source": "J-PlatPat", "operation": args.operation, "queried_at": started}
    if args.operation == "search":
        context.update(kind=args.kind, query=args.query, result_limit=args.limit)
    elif args.operation == "patent-claims":
        context.update(document=args.key)
    elif args.operation == "case-status":
        context.update(kind=args.kind, application_number=args.application_number, current_legal_status_verified=False)
    else:
        context.update(document_id=args.document_id)
    try:
        if args.operation == "search":
            endpoint = SEARCH_ENDPOINTS[args.kind]
            payload = search_payload(args.kind, args.query, args.limit)
            cookies = client.anonymous_session(endpoint, "S0000") if args.kind == "patent" else None
            result = summarize_search(args.kind, client.post(endpoint, payload, "search", cookies))
        elif args.operation == "patent-claims":
            result = patent_claims(client, args.key)
        elif args.operation == "case-status":
            result = case_status(client, args.kind, args.application_number)
        else:
            result = design_pdf(client, args.document_id)
    except QueryError as exc:
        result = {"status": exc.status, "error": str(exc)}
    result = {**context, **result, "requests": client.trace}
    path = args.out / "result.json"
    save_json(path, result)
    summary = {key: value for key, value in result.items() if key not in {"candidates", "requests"}}
    print(json.dumps({**summary, "result_file": str(path.resolve())}, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ok", "no_result"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
