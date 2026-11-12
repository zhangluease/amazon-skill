#!/usr/bin/env python3
"""Validate saved ABA query pages and prepare the existing Excel export input."""

import argparse
import json
from pathlib import Path

HEADERS = ["日文关键词", "来源", "ABA搜索部门", "ABA搜索频率排名", "ABA点击ASIN",
           "ABA点击份额排名", "ABA点击份额", "ABA转化份额", "统计周期", "数据开始日期",
           "数据结束日期", "命中竞品ASIN", "命中种子词", "报表ID", "查询ID", "采集覆盖"]
REPORT_TYPE = "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT"


def convert(document):
    records = {}
    statuses = []
    queries = document.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("Provide a nonempty queries array.")
    for query in queries:
        pages = query.get("pages", [])
        if not isinstance(pages, list) or not pages:
            raise ValueError("Every query needs its latest status or result pages.")
        first = pages[0]
        query_id = first["queryId"]
        report_id = first["reportId"]
        expected_cursor = None
        count = 0
        status = first["status"]
        if status not in {"DONE", "PARTIAL", "IN_PROGRESS", "FAILED"}:
            raise ValueError("Unknown query status.")
        for index, page in enumerate(pages):
            if (page.get("queryId") != query_id or page.get("reportId") != report_id
                    or page.get("reportType") != REPORT_TYPE
                    or page.get("marketplaceId") != "A1VC38T7YXB528"
                    or page.get("status") != status
                    or page.get("rowsMatched") != first.get("rowsMatched")
                    or page.get("coverageComplete") != first.get("coverageComplete")
                    or page.get("dataStartTime") != first.get("dataStartTime")
                    or page.get("dataEndTime") != first.get("dataEndTime")):
                raise ValueError("Pages must describe the same Japanese Search Terms query snapshot.")
            if index and expected_cursor is None:
                raise ValueError("Unexpected page after the final page.")
            if page.get("cursor") != expected_cursor:
                raise ValueError("Missing, reordered or duplicated query page.")
            page_rows = page.get("rows", [])
            if not isinstance(page_rows, list) or (status in {"IN_PROGRESS", "FAILED"} and page_rows):
                raise ValueError("Unfinished or failed queries cannot publish result rows.")
            count += len(page_rows)
            expected_cursor = page.get("nextCursor")
        pagination_complete = expected_cursor is None and status in {"DONE", "PARTIAL"}
        if pagination_complete and count != first.get("rowsMatched"):
            raise ValueError("Returned row count does not match the completed query.")
        full = status == "DONE" and first.get("coverageComplete") is True and pagination_complete
        if full and count == 0:
            collection_status = "no_matches"
        elif full:
            collection_status = "complete"
        elif status in {"DONE", "PARTIAL"}:
            collection_status = "partial"
        else:
            collection_status = status.lower()
        statuses.append({"queryId": query_id, "reportId": report_id,
                         "status": collection_status, "serverStatus": status,
                         "coverageComplete": full, "paginationComplete": pagination_complete,
                         "rowsReturned": count, "rowsMatched": first.get("rowsMatched"),
                         "error": first.get("error"),
                         "truncationReason": first.get("truncationReason")})
        period = query.get("reportPeriod") or first.get("reportPeriod") or "未返回"
        if period not in {"DAY", "WEEK", "MONTH", "QUARTER", "未返回"}:
            raise ValueError("Unknown reporting period.")
        for page in pages:
            for row in page.get("rows", []):
                keyword = row["searchTerm"].strip()
                if not keyword or not row["departmentName"]:
                    raise ValueError("Missing keyword or department.")
                for field in ("clickShare", "conversionShare"):
                    value = row.get(field)
                    if value is not None and (type(value) not in (int, float) or not 0 <= value <= 1):
                        raise ValueError("ABA shares must be fractional values between zero and one.")
                key = (report_id, row["departmentName"], keyword, row["clickedAsin"])
                values = [keyword, "ABA全站搜索词", row["departmentName"], row["searchFrequencyRank"],
                          row["clickedAsin"], row["clickShareRank"], row.get("clickShare"),
                          row.get("conversionShare"), period, first.get("dataStartTime"),
                          first.get("dataEndTime")]
                old = records.get(key)
                if old is not None and old["values"] != values:
                    raise ValueError("Conflicting ABA metrics for the same report, department, term and ASIN.")
                if old is None:
                    old = records[key] = {"values": values, "asins": set(), "seeds": set(),
                                          "queryIds": set(), "coverage": set()}
                old["asins"].update(row.get("matchedAsins", []))
                old["seeds"].update(row.get("matchedSeedTerms", []))
                old["queryIds"].add(query_id)
                old["coverage"].add("完整" if full else "部分")
    rows = []
    for (report_id, _department, _keyword, _asin), record in records.items():
        rows.append(record["values"] + ["、".join(sorted(record["asins"])),
                    "、".join(sorted(record["seeds"])), report_id,
                    "、".join(sorted(record["queryIds"])), "、".join(sorted(record["coverage"]))])
    return {"headers": HEADERS, "rows": rows,
            "percentage_columns": ["ABA点击份额", "ABA转化份额"],
            "query_statuses": statuses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        table = convert(json.loads(args.input.read_text(encoding="utf-8-sig")))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + ".partial")
        temporary.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(args.output)
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Invalid ABA query pages: {error}\n")
    print(json.dumps({"output": str(args.output), "detail_rows": len(table["rows"]),
                      "queries": table["query_statuses"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
