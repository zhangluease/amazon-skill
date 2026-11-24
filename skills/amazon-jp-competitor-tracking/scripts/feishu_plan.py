"""Plan existing Feishu rows in title-empty-first order, without network or writes."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, parse_qs

from competitor_core import ApiError, parse_asin, write_json


def parse_table_url(value):
    try:
        url = urlsplit(value)
        query = parse_qs(url.query)
        if (url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.feishu.cn')
                or url.username or url.password or url.port not in (None, 443)
                or not re.fullmatch(r'/(wiki|base)/[A-Za-z0-9]+', url.path)):
            raise ValueError()
        table, view = query.get('table', []), query.get('view', [])
        if len(table) != 1 or not re.fullmatch(r'tbl[A-Za-z0-9]+', table[0]):
            raise ValueError()
        if len(view) > 1 or view and not re.fullmatch(r'vew[A-Za-z0-9]+', view[0]):
            raise ValueError()
        return {'url': value, 'table_id': table[0], 'view_id': view[0] if view else None}
    except (ValueError, TypeError):
        raise ApiError('INVALID_FEISHU_TABLE_URL') from None


def cell_text(value):
    return raw_cell_text(value).strip()


def raw_cell_text(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(raw_cell_text(item) for item in value)
    if isinstance(value, dict) and isinstance(value.get('text'), str):
        return value['text']
    raise ApiError('INVALID_FEISHU_TEXT_CELL')


def create_plan(records, target, *, title_column='标题', asin_column='ASIN', link_column='链接', columns=None):
    target = parse_table_url(target)
    if columns is None or title_column not in columns or asin_column not in columns:
        raise ApiError('FEISHU_REQUIRED_COLUMNS_NOT_CONFIRMED')
    if not isinstance(records, list):
        raise ApiError('INVALID_FEISHU_RECORDS')
    new, update, invalid, seen = [], [], [], set()
    for row in records:
        if not isinstance(row, dict):
            raise ApiError('INVALID_FEISHU_RECORDS')
        record_id = row.get('record_id')
        if not isinstance(record_id, str) or not re.fullmatch(r'rec[A-Za-z0-9_]+', record_id) or record_id in seen:
            raise ApiError('INVALID_OR_DUPLICATE_FEISHU_RECORD_ID')
        seen.add(record_id)
        fields = row.get('fields', row)
        if not isinstance(fields, dict):
            raise ApiError('INVALID_FEISHU_RECORDS')
        try:
            title = cell_text(fields.get(title_column))
            raw_asin = cell_text(fields.get(asin_column))
            asin = parse_asin(raw_asin) if raw_asin else None
            link = fields.get(link_column)
            if isinstance(link, dict):
                link = link.get('link') or link.get('url') or link.get('text')
            link = cell_text(link)
            if link:
                markdown = re.fullmatch(r'\[[^\]]*\]\((https://[^)]+)\)', link)
                linked_asin = parse_asin(markdown[1] if markdown else link)
                if asin and asin != linked_asin:
                    raise ApiError('FEISHU_ASIN_LINK_MISMATCH')
                asin = asin or linked_asin
            if asin is None:
                raise ApiError('MISSING_FEISHU_ASIN')
            item = {'record_id': record_id, 'asin': asin, 'title_at_read': title}
            (update if title else new).append(item)
        except ApiError as error:
            invalid.append({'record_id': record_id, 'code': error.code})
    new_asins = list(dict.fromkeys(row['asin'] for row in new))
    new_set = set(new_asins)
    update_asins = list(dict.fromkeys(row['asin'] for row in update if row['asin'] not in new_set))
    return {'format': 'feishu-competitor-plan-v1', 'target': target,
            'columns': {'title': title_column, 'asin': asin_column, 'link': link_column},
            'record_count': len(records), 'new_rows': new, 'update_rows': update, 'invalid_rows': invalid,
            'new_asins': new_asins, 'update_fetch_asins': update_asins,
            'unique_asins': new_asins + update_asins,
            'phase_order': ['new_rows', 'update_rows'],
            'write_operation': 'update_existing_records_only',
            'reuse_policy': 'same_run_snapshot_for_duplicate_asins; never_reuse_previous_task_data'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--records', type=Path, required=True, help='NDJSON artifact from official CLI')
    parser.add_argument('--columns', type=Path, required=True, help='Confirmed column names as JSON array')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        rows = [json.loads(line) for line in args.records.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        plan = create_plan(rows, args.url, columns=json.loads(args.columns.read_text(encoding='utf-8-sig')))
        write_json(args.out, plan)
        print(json.dumps({'records': plan['record_count'], 'new_rows': len(plan['new_rows']),
                          'update_rows': len(plan['update_rows']), 'invalid_rows': len(plan['invalid_rows']),
                          'unique_asins': len(plan['unique_asins'])}))
        return 0
    except ApiError as error:
        print(json.dumps({'status': 'failed', 'category': error.code}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print(json.dumps({'status': 'failed', 'category': 'INVALID_FEISHU_LOCAL_INPUT_OR_OUTPUT'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
