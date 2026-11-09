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
CASE_PROCEDURE = "/app/docgw/wsh0102"
CASE_REGISTRATION = "/app/docgw/wsh0105"
CASE_TRIALS = "/app/docgw/wsh0106"
CASE_TYPES = {
    "patent": ("特願", "1", "PU", "10"),
    "utility-model": ("実願", "2", "PU", "20"),
    "design": ("意匠出願", "3", "DE", "30"),
}
SEARCH_RIGHT_TYPES = {"P": "patent", "U": "utility-model"}
PATENT_STAGES = {
    "10": "審査請求前",
    "21": "審査中",
    "22": "査定不服",
    "40": "特許 有効",
    "50": "異議申立中・無効審判中",
    "61": "（出願の）却下・拒絶",
    "62": "特許 消滅",
}
PATENT_TRIAL_TYPES = {
    **dict.fromkeys(("10", "11", "112", "113", "12", "122", "123", "15", "16"), "無効審判"),
    "40": "訂正・訂正無効審判",
    "41": "訂正審判",
    "43": "訂正無効",
    **dict.fromkeys(("65", "651", "652"), "登録異議申立"),
    "80": "査定不服審判",
}
GAZETTE_YEAR = r"(?:[0-9]{4}|[明大昭平令][0-9]{1,2})"
GAZETTE_KEYS = {
    "patent": re.compile(rf"特許[0-9]{{1,8}}|特[開公]{GAZETTE_YEAR}-[0-9]{{1,6}}"),
    "utility-model": re.compile(rf"実登[0-9]{{1,7}}|実[開表公全]{GAZETTE_YEAR}-[0-9]{{1,6}}"),
}
RESULT_LIMIT = 500
UPSTREAM_SEARCH_LIMIT = 3000
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


def search_payload(kind, query, auto_narrow=False):
    payload = {
        "DISP_ID": "S0000",
        "SEARCH_TYPE": "1",
        "SIMPLE_SEARCH_DISP": {
            "AUTO_NARROW_FLG": int(auto_narrow),
            "SEARCH_COND": [{"NUM_TYPE": [], "KEYWD": word} for word in query.split()],
            "SEARCH_OPTN": {
                "FILTER_INFO": {**SEARCH_FILTERS[kind], "ITEM": None, "COND": None, "COMPAR_COND_SIGN": None}
            },
        },
        "SEARCH_RSLT_MAX_CNT": UPSTREAM_SEARCH_LIMIT,
        "NUM_LST_MAX_CNT": 0 if kind == "patent" else UPSTREAM_SEARCH_LIMIT,
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
    if match:
        return "".join(match.groups())
    if kind == "utility-model":
        era = re.fullmatch(r"(?:実願)?([明大昭平令])([0-9]{1,2})-([0-9]{1,6})", value)
        if era:
            offset, last_year = {"明": (1867, 45), "大": (1911, 15), "昭": (1925, 64),
                                 "平": (1988, 31), "令": (2018, 99)}[era[1]]
            year = int(era[2])
            if 1 <= year <= last_year:
                return f"{offset + year}{era[3].zfill(6)}"
    return None


def case_reference(kind, digits):
    if not digits or (kind == "design" and digits[4] == "5"):
        return {}
    prefix, _, category, url_type = CASE_TYPES[kind]
    number = f"{digits[:4]}-{digits[4:]}"
    link_number = number
    year = int(digits[:4])
    if kind == "utility-model" and 1990 <= year <= 1999:
        link_number = f"H{year - 1988:02d}-{digits[4:]}"
    return {
        "application_number": prefix + number,
        "case_url": f"{BASE_URL}/c1801/{category}/JP-{link_number}/{url_type}/ja",
    }


def candidate(kind, row):
    common = {
        "right_type": kind,
        "application_number": row.get("APP_NUM_DISP") or row.get("APP_NUM"),
        "registration_number": row.get("REG_NUM_DISP") or row.get("REG_NUM"),
        "applicants_or_holders": row.get("APPN_RIGHT_HOLDER"),
        "filing_date": row.get("APP_DATE"),
    }
    reference = case_reference(kind, application_digits(kind, common["application_number"]))
    if reference:
        common["case_url"] = reference["case_url"]
    if kind in GAZETTE_KEYS:
        return {
            **common,
            "title": row.get("INVEN_NAME"),
            "publication_number": row.get("PUBLI_NUM_DISP"),
            "gazette_keys": [
                row[field + "_DISP"] for field in ("REG_NUM", "PUBLI_NUM", "EXAM_PUB_NUM")
                if row.get(field + "_DOCU_EXISTS_FLG") == 1
                and GAZETTE_KEYS[kind].fullmatch(row.get(field + "_DISP") or "")
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


def summarize_search(kind, data, limit=RESULT_LIMIT):
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
    counts = {"patent": 0, "utility-model": 0}
    unknown = 0
    selected = rows[:limit]
    for index, row in enumerate(selected):
        right_type = kind
        if kind == "patent":
            law = row.get("LAW_DIVISION")
            right_type = SEARCH_RIGHT_TYPES.get(law) if isinstance(law, str) else None
            if right_type is None:
                unknown += 1
                continue
            counts[right_type] += 1
        candidates.append({"source_row_index": index, **candidate(right_type, row)})
    narrowing = info.get("AUTO_NARROW_EXEC")
    narrowed = isinstance(narrowing, dict) and bool(narrowing.get("AUTO_NARROW_PREV_COUNT_CNT"))
    complete = code == 0 and len(selected) == hit_count and len(selected) == len(rows) and not narrowed and unknown == 0
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
        "rows_selected": len(selected),
        "locally_truncated_rows": len(rows) - len(selected),
        "candidates_in_scope": len(candidates),
        "patent_candidates": counts["patent"],
        "utility_model_candidates": counts["utility-model"],
        "unclassified_patent_rows": unknown,
        "server_auto_narrowing": narrowing,
        "complete_within_query": complete,
        "candidates": candidates,
    }


def request_search(client, kind, query, name, auto_narrow=False):
    endpoint = SEARCH_ENDPOINTS[kind]
    cookies = client.anonymous_session(endpoint, "S0000") if kind == "patent" else None
    return client.post(endpoint, search_payload(kind, query, auto_narrow), name, cookies)


def search_candidates(client, kind, query, limit):
    result = summarize_search(kind, request_search(client, kind, query, "search"), limit)
    exceeds_site_limit = (
        result["status"] == "needs_refinement" and result["message_id"] == "C90003-E"
        and (result["server_hits"] or 0) > UPSTREAM_SEARCH_LIMIT
    )
    if not exceeds_site_limit:
        return result

    # Use the site's documented date narrowing only when the original list is unavailable.
    try:
        data = request_search(client, kind, query, "search-narrowed", auto_narrow=True)
        narrowed = summarize_search(kind, data, limit)
    except QueryError as exc:
        return {**result, "narrowing_failure": {"status": exc.status, "error": str(exc)}}
    return {
        **narrowed,
        "status": "partial" if narrowed["status"] in {"ok", "no_result"} else narrowed["status"],
        "server_hits": result["server_hits"],
        "hits_after_auto_narrowing": narrowed["server_hits"],
        "initial_message_id": result["message_id"],
        "complete_within_query": False,
    }


def single_record(data, field):
    records = data.get(field)
    if isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict):
        return records[0]
    return {}


def case_response_row(data):
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
    if not row:
        raise QueryError("J-PlatPat did not return one case response")
    return row


def require_case_identity(application, kind, digits):
    if application.get("law_cd") != CASE_TYPES[kind][1] or application.get("app_num") != digits:
        raise QueryError("Case identity is missing, ambiguous, or does not match the requested application")


def summarize_case_status(kind, digits, data):
    row = case_response_row(data)
    application = single_record(single_record(row, "app_info"), "app_art")
    require_case_identity(application, kind, digits)

    registration = single_record(row, "reg_info")
    legal = row.get("status")
    stage = legal.get("stage") if isinstance(legal, dict) else None
    event = legal.get("detailed_event") if isinstance(legal, dict) else None
    label = PATENT_STAGES.get(stage) if kind == "patent" and isinstance(stage, str) else None
    result = {
        "status": "ok" if label else "partial",
        **case_reference(kind, digits),
        "registration_number": single_record(registration, "reg_art").get("reg_num"),
        "stage_code": stage,
        "stage_label": label,
        "detailed_event_code": event,
        "detailed_event_label": "年金納付" if label and event == "U110" else None,
        "current_legal_status_verified": label is not None,
    }
    if kind == "utility-model":
        details = single_record(registration, "reg_detail_items_art")
        result.update(extinction_date=case_date(details.get("right_disppr_date")),
                      cancellation_code=details.get("right_ersr_id"))
    return result


def request_case(client, kind, digits, endpoint, name):
    law = CASE_TYPES[kind][1]
    payload = {
        "displayId": "H0000", "searchCondition": {"lawCd": law, "applicationNumber": digits}, "OTID": None,
    }
    cookies = client.anonymous_session(endpoint, "H0000", f"{law}-{digits}")
    return client.post(endpoint, payload, name, cookies)


def case_records(data, field):
    records = data.get(field)
    if records is None:
        return []
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise QueryError(f"Invalid J-PlatPat case records: {field}")
    return records


def case_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def case_registration(row, kind, digits, registration_number):
    record = single_record(row, "reg_info")
    application = single_record(single_record(record, "app_art"), "app")
    require_case_identity(application, kind, digits)
    registration = single_record(single_record(record, "reg_art"), "reg")
    if registration.get("reg_num") != registration_number:
        raise QueryError("Registration number does not match the case status response")
    holders = [
        entry["right_holder_name"]
        for group in case_records(record, "right_holder_art")
        for entry in case_records(group, "right_holder")
        if entry.get("right_holder_name")
    ]
    details = single_record(single_record(record, "reg_detail_items_art"), "reg_detail_items")
    paid_year = record.get("last_pymnt_yearly_art")
    paid_year = int(paid_year) if isinstance(paid_year, str) and re.fullmatch(r"[0-9]{1,2}", paid_year) else 0
    return {
        "registered_holders": holders,
        "registration_date": case_date(registration.get("reg_date")),
        "record_updated_date": case_date(record.get("update_date")),
        "last_paid_year_number": paid_year or None,
        "maximum_term_end_date": case_date(details.get("conti_prd_expire_date")),
        "extinction_date": case_date(details.get("right_disppr_date")),
        "cancellation_code": details.get("right_ersr_id"),
    }


def case_procedure(row, kind, digits, registration_number):
    application = single_record(single_record(row, "app_info"), "app_art")
    require_case_identity(single_record(application, "app"), kind, digits)
    registration = single_record(single_record(single_record(row, "reg_info"), "reg_art"), "reg")
    if registration.get("reg_num") != registration_number:
        raise QueryError("Procedure registration does not match the case status response")
    trial_group = single_record(single_record(row, "appl_info"), "appl_art")
    return {
        "examination_records_returned": len(case_records(application, "exam_rec_pu")),
        "registration_records_returned": len(case_records(registration, "reg_rec")),
        "trial_references_returned": len(case_records(trial_group, "appl_art")),
    }


def case_trials(row, kind, digits, registration_number):
    trials = []
    if "appl_info" not in row:
        raise QueryError("Missing J-PlatPat trial records field")
    for record in case_records(row, "appl_info"):
        require_case_identity(single_record(single_record(record, "app_art"), "app"), kind, digits)
        registration = single_record(single_record(record, "reg_art"), "reg")
        if registration_number and registration.get("reg_num") != registration_number:
            raise QueryError("Trial registration does not match the case status response")
        trial = single_record(single_record(record, "appl_art"), "appl")
        number = trial.get("appl_num")
        if not isinstance(number, str) or not re.fullmatch(r"[0-9]{10}", number):
            raise QueryError("Missing J-PlatPat trial number")
        trial_type = trial.get("appl_typ")
        decision_info = single_record(record, "jdgmnt_pub_info_art")
        dates = [case_date(entry.get("jdgmnt_date")) for entry in case_records(decision_info, "jdgmnt_pub_info")]
        trials.append({
            "trial_number": f"{number[:4]}-{number[4:]}",
            "type_code": trial_type,
            "type_label": PATENT_TRIAL_TYPES.get(trial_type) if isinstance(trial_type, str) else None,
            "filing_date": case_date(trial.get("appl_clim_date")),
            "decision_dates": [date for date in dates if date],
            "final_disposition_code": trial.get("appl_final_dspst_typ"),
            "final_disposition_date": case_date(trial.get("appl_final_dspst_define_date")),
        })
    return {"trials": trials}


def case_status(client, kind, digits, history=False):
    if not case_reference(kind, digits):
        raise QueryError("This design application uses a different case route", "material_unavailable")
    result = summarize_case_status(kind, digits, request_case(client, kind, digits, CASE_INFO, "case"))
    if not history:
        return result

    sections = (
        ("registration", CASE_REGISTRATION, case_registration),
        ("procedure", CASE_PROCEDURE, case_procedure),
        ("trials", CASE_TRIALS, case_trials),
    )
    result["history"] = {}
    registration_number = result["registration_number"]
    for name, endpoint, summarize in sections:
        if name == "registration" and not registration_number:
            result["history"][name] = {"status": "not_requested", "reason": "No registration number in case status"}
            continue
        try:
            row = case_response_row(request_case(client, kind, digits, endpoint, name))
            result["history"][name] = {"status": "ok", **summarize(row, kind, digits, registration_number)}
        except QueryError as exc:
            result["history"][name] = {"status": exc.status, "error": str(exc)}
            result["status"] = "partial"
            if exc.status in {"access_limited", "verification_required"}:
                break
    return result


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
    kind = "utility-model" if key.startswith("実") else "patent"
    digits = application_digits(kind, doc.get("PATENT_APP_NUM"))
    document_kind = "grant_gazette" if key.startswith(("特許", "実登")) else "publication_gazette"
    return {
        "status": "ok" if complete else "partial",
        **case_reference(kind, digits),
        "right_type": kind,
        "document": key,
        "claims_file": "claims.txt",
        "document_kind": document_kind,
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
    search = commands.add_parser("search", help="search Japanese patents and utility models, or designs")
    search.add_argument("kind", choices=SEARCH_ENDPOINTS)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=RESULT_LIMIT, help=f"local result cap (1–{RESULT_LIMIT})")
    case = commands.add_parser("case-status", help="read case information for an exact Japanese application")
    case.add_argument("kind", choices=CASE_TYPES)
    case.add_argument("application_number")
    case.add_argument("--history", action="store_true", help="also read patent or utility model history")
    commands.add_parser("patent-claims", help="read claims for one exact patent or utility model gazette").add_argument("key")
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
        if not any(pattern.fullmatch(args.key) for pattern in GAZETTE_KEYS.values()):
            parser.error("use an exact Japanese patent or utility model gazette number (e.g. 特許 or 実登)")
    elif args.operation == "case-status":
        args.application_number = application_digits(args.kind, args.application_number)
        if not args.application_number:
            parser.error("use a Japanese application number, e.g. 特願2012-184735 or 実願平10-004399")
        if args.history and args.kind == "design":
            parser.error("--history currently supports Japanese patents and utility models")
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
            result = search_candidates(client, args.kind, args.query, args.limit)
        elif args.operation == "patent-claims":
            result = patent_claims(client, args.key)
        elif args.operation == "case-status":
            result = case_status(client, args.kind, args.application_number, args.history)
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
