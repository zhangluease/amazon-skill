#!/usr/bin/env python3
"""Fetch Japanese Amazon search-box suggestions and export them to Excel."""

import argparse
import csv
from http.client import HTTPException
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from export_xlsx import write_xlsx


ENDPOINT = "https://completion.amazon.co.jp/api/2017/suggestions"
SOURCE = "amazon_jp_autocomplete"
HEADERS = ("source", "seed", "position", "keyword")


def fetch_suggestions(seed):
    params = {
        "prefix": seed,
        "alias": "aps",
        "mid": "A1VC38T7YXB528",
        "market-id": "6",
        "client-info": "amazon-search-ui",
        "lop": "ja_JP",
    }
    request = Request(f"{ENDPOINT}?{urlencode(params)}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        payload = json.load(response)

    items = payload.get("suggestions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("Missing suggestions array")

    suggestions = []
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError("Invalid suggestion item")
        if item.get("type") != "KEYWORD":
            continue
        keyword = item.get("value")
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("Missing keyword value")
        suggestions.append((position, keyword))
    return suggestions


def write_tsv(output, rows):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".autocomplete-", dir=output.parent) as directory:
        temporary = Path(directory) / "suggestions.tsv"
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(HEADERS)
            writer.writerows(rows)
        temporary.replace(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seeds", nargs="+", help="Japanese search prefixes")
    parser.add_argument("--output", required=True, type=Path, help="Path to the .xlsx workbook or explicit .tsv export")
    args = parser.parse_args(argv)
    if any(not seed.strip() for seed in args.seeds):
        parser.error("Each seed must contain at least one non-whitespace character.")
    extension = args.output.suffix.lower()
    if extension not in {".xlsx", ".tsv"}:
        parser.error("Output must have an .xlsx or .tsv extension.")

    rows = []
    results = []
    completed = 0
    blocked = False
    for seed in dict.fromkeys(args.seeds):
        result = {"seed": seed}
        results.append(result)
        if blocked:
            result["status"] = "skipped"
            continue
        try:
            suggestions = fetch_suggestions(seed)
        except HTTPError as error:
            result.update(status="http_error", http_status=error.code)
            blocked = error.code in (403, 429)
            error.close()
        except (OSError, HTTPException):
            result["status"] = "network_error"
        except ValueError:
            result["status"] = "invalid_response"
        else:
            completed += 1
            rows.extend((SOURCE, seed, position, keyword) for position, keyword in suggestions)
            result.update(status="ok" if suggestions else "no_matches", count=len(suggestions))

    if completed:
        if extension == ".xlsx":
            write_xlsx(args.output, HEADERS, rows)
        else:
            write_tsv(args.output, rows)
    print(json.dumps({
        "output": str(args.output) if completed else None,
        "count": len(rows),
        "seeds": results,
    }, ensure_ascii=False))
    return 0 if completed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
