"""Official lark-cli bridge and schema-aware, record-ID-only competitor updates."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from competitor_core import ApiError, CHANGE_SCHEMA, write_json, is_login_mask
from feishu_plan import cell_text, parse_table_url, create_plan


def find_cli(supplied=None):
    if supplied or os.environ.get('AMAZON_COMPETITOR_FEISHU_CLI'):
        path = Path(supplied or os.environ['AMAZON_COMPETITOR_FEISHU_CLI']).expanduser()
    else:
        version = json.loads((Path(__file__).parents[1] / 'package.json').read_text())['feishu_cli_version']
        binary = 'lark-cli.exe' if os.name == 'nt' else 'lark-cli'
        path = Path.home() / f'.amazon-skill/cache/feishu-cli/{version}/node_modules/@larksuite/cli/bin/{binary}'
        if not path.is_file():
            found = shutil.which('lark-cli')
            path = Path(found) if found else path
            resolved = path.resolve()
            if resolved.suffix.lower() == '.js':
                native = resolved.parents[1] / 'bin' / binary
                if native.is_file():
                    path = native
            elif resolved.suffix.lower() == '.cmd':
                native = resolved.parent / 'node_modules' / '@larksuite/cli' / 'bin' / binary
                if native.is_file():
                    path = native
    if not path.is_file() or path.suffix.lower() in ('.cmd', '.bat'):
        raise ApiError('FEISHU_CLI_REQUIRED')
    return str(path.resolve())


class FeishuClient:
    def __init__(self, output, cli=None):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.cli = find_cli(cli)

    def run(self, args, *, artifact=False):
        env = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER='1', LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
        try:
            result = subprocess.run([self.cli, *args, '--as', 'user'], cwd=self.output,
                                    capture_output=True, text=True, encoding='utf-8', env=env, timeout=90)
            data = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise ApiError('FEISHU_CLI_FAILED') from None
        if result.returncode != 0:
            error = data.get('error', {})
            category = error.get('subtype') or error.get('type') or 'FAILED'
            raise ApiError('FEISHU_' + re.sub(r'[^A-Z0-9_]', '_', str(category).upper()))
        if artifact:
            if data.get('manifest_version') != 'v1' or not isinstance(data.get('records_count'), int):
                raise ApiError('INVALID_FEISHU_ARTIFACT')
            return data
        if data.get('ok') is not True or data.get('identity') != 'user':
            raise ApiError('INVALID_FEISHU_RESPONSE_OR_IDENTITY')
        return data.get('data')

    def resolve(self, url):
        target = parse_table_url(url)
        token = url.split('?', 1)[0].rsplit('/', 1)[1]
        if '/wiki/' in url:
            # This documented endpoint works with the granted wiki:wiki:readonly scope.
            node = self.run(['api', 'GET', '/open-apis/wiki/v2/spaces/get_node',
                             '--params', json.dumps({'token': token})]).get('node', {})
            if node.get('obj_type') != 'bitable' or node.get('node_token') != token:
                raise ApiError('FEISHU_TARGET_IS_NOT_BITABLE')
            token = node.get('obj_token')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9]+', token):
            raise ApiError('INVALID_FEISHU_BASE_TOKEN')
        return dict(target, base_token=token)

    def fields(self, target):
        data = self.run(['base', '+field-list', '--base-token', target['base_token'],
                         '--table-id', target['table_id']])
        fields = data.get('fields')
        if not isinstance(fields, list) or any(not isinstance(f, dict) for f in fields):
            raise ApiError('INVALID_FEISHU_FIELD_SCHEMA')
        return fields

    def records(self, target, fields, record_ids=None):
        if record_ids is not None and (not record_ids or any(not re.fullmatch(r'rec[A-Za-z0-9_]+', value)
                                                           for value in record_ids)):
            raise ApiError('INVALID_FEISHU_RECORD_ID')
        rows, offset, revision, seen = [], 0, None, set()
        while True:
            name = 'records-' + uuid.uuid4().hex + '.ndjson'
            args = ['base', '+record-get' if record_ids is not None else '+record-list',
                    '--base-token', target['base_token'], '--table-id', target['table_id'],
                    '--format', 'ndjson', '--output', name]
            if record_ids is not None:
                for record_id in record_ids:
                    args.extend(['--record-id', record_id])
            else:
                args.extend(['--limit', '2000', '--offset', str(offset)])
                if target.get('view_id'):
                    args.extend(['--view-id', target['view_id']])
            for field in fields:
                args.extend(['--field-id', field['id']])
            manifest = self.run(args, artifact=True)
            if manifest.get('base_token') != target['base_token'] or manifest.get('table_id') != target['table_id']:
                raise ApiError('FEISHU_ARTIFACT_TARGET_MISMATCH')
            if revision is not None and manifest.get('rev') != revision:
                raise ApiError('FEISHU_SNAPSHOT_CHANGED')
            revision = manifest.get('rev')
            batch = [json.loads(line) for line in (self.output / name).read_text().splitlines() if line.strip()]
            if len(batch) != manifest['records_count']:
                raise ApiError('INCOMPLETE_FEISHU_RECORDS')
            for row in batch:
                record_id = row.get('record_id')
                if not isinstance(record_id, str) or record_id in seen:
                    raise ApiError('INVALID_OR_DUPLICATE_FEISHU_RECORD_ID')
                seen.add(record_id)
            rows.extend(batch)
            if manifest.get('has_more') is False:
                return rows
            next_offset = manifest.get('next_offset')
            if record_ids is not None or not isinstance(next_offset, int) or next_offset <= offset:
                raise ApiError('INCOMPLETE_FEISHU_RECORDS')
            offset = next_offset

    def update(self, target, updates):
        if not updates or len(updates) > 200:
            raise ApiError('FEISHU_BATCH_MUST_BE_1_TO_200')
        name = 'patch-' + uuid.uuid4().hex + '.json'
        write_json(self.output / name, {'update_records': updates})
        data = self.run(['base', '+record-batch-update', '--base-token', target['base_token'],
                         '--table-id', target['table_id'], '--json', '@' + name])
        if data is not None and not isinstance(data, dict):
            raise ApiError('INVALID_FEISHU_UPDATE_RESPONSE')
        data = data or {}
        if data.get('ignored_fields'):
            raise ApiError('FEISHU_FIELDS_WERE_IGNORED')
        return data


TEXT_FIELDS = {'标题': 'title', '品牌': 'brand', '父体销量': 'parent_sales_30d',
               '子体销量': 'child_monthly_purchased', '评论数量': 'review_count',
               '评论分数': 'rating', '变体数': 'variation_count', '链接': 'url'}


def build_patch(product, before, schema):
    """Only known, evidenced values are written; unknowns preserve existing cells."""
    if any(is_login_mask(value) for value in product.values()):
        raise ApiError('FEISHU_SOURCE_REQUIRES_LOGIN')
    if product.get('asin') != cell_text(before.get('ASIN')) or not product.get('title'):
        raise ApiError('FEISHU_PRODUCT_IDENTITY_MISMATCH')
    fields = {field['name']: field for field in schema}
    values, skipped = {}, []
    def put(name, value, expected_type):
        field = fields.get(name)
        if not field or field.get('type') != expected_type:
            skipped.append({'field': name, 'reason': 'missing_or_incompatible_schema'})
        elif value is None:
            skipped.append({'field': name, 'reason': 'source_unknown'})
        elif expected_type == 'select' and any(option not in [item['name'] for item in field.get('options', [])]
                                                for option in value):
            raise ApiError('FEISHU_SELECT_OPTION_NOT_PRESENT')
        else:
            values[name] = value
    for name, key in TEXT_FIELDS.items():
        value = product.get(key)
        put(name, f'{value:g}' if isinstance(value, (float, int)) else value, 'text')
    put('当前售价', f'￥{product["price"]:,.0f}' if product.get('price') is not None else None, 'text')
    previous_price = re.fullmatch(r'\s*[￥¥]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*', cell_text(before.get('当前售价')))
    baseline = float(previous_price[1].replace(',', '')) if previous_price else None
    fluctuation = (abs(product['price'] - baseline) / baseline > .10
                   if baseline and product.get('price') is not None else None)
    put('价格波动（超10%）', ['是' if fluctuation else '否'] if type(fluctuation) is bool else None, 'select')
    put('大类排名', f'#{product["root_rank"]:g} {product.get("root_category") or ""}'.strip()
        if product.get('root_rank') is not None else None, 'text')
    put('小类排名', '；'.join(f'#{item["rank"]} {item.get("label") or ""}'.strip()
        for item in product['sub_ranks']) if product.get('sub_ranks') else None, 'text')
    put('折扣比例', f'-{product["discount_percent"]:g}%' if product.get('discount_percent') is not None else None, 'text')
    put('数量装', product.get('package_quantity'), 'number')
    stock = product.get('stock')
    put('库存状态（是/否）断货', ['是' if stock == 'out_of_stock' else '否']
        if stock in ('in_stock', 'out_of_stock') else None, 'select')
    original = product.get('title_original_claim')
    put('是否原装', ['是' if original else '否'] if type(original) is bool else None, 'select')
    history = product.get('history', {})
    marker = product.get('media_change_observed') if history.get('schema') == CHANGE_SCHEMA else None
    if history.get('status') not in ('ok', 'partial') or marker is False and history.get('status') != 'ok':
        marker = None
    put('图片变化', ['是' if marker else '否'] if type(marker) is bool else None, 'select')
    # These need separate semantics/evidence; do not replace them with another price field.
    skipped.extend({'field': name, 'reason': 'no_verified_source_for_this_field'}
                   for name in ('历史价格', '活动价', '活动期间（是/否）') if name in fields)
    return {'fields': values, 'by_id': {fields[name]['id']: value for name, value in values.items()},
            'skipped': skipped}


def apply_one(client, target, schema, before, product):
    record_id = before['record_id']
    patch = build_patch(product, before, schema)
    current = client.records(target, schema, [record_id])
    if len(current) != 1 or current[0]['record_id'] != record_id:
        raise ApiError('FEISHU_RECORD_NOT_FOUND')
    watched = set(patch['fields']) | {'ASIN', '标题'}
    if any(current[0].get(name) != before.get(name) for name in watched):
        raise ApiError('FEISHU_RECORD_CHANGED_SINCE_READ')
    write_json(client.output / ('before-' + record_id + '.json'), current[0])
    client.update(target, {record_id: patch['by_id']})
    verified = None
    for delay in (0, 1, 2):
        if delay:
            time.sleep(delay)
        rows = client.records(target, schema, [record_id])
        if len(rows) == 1 and all(rows[0].get(name) == value for name, value in patch['fields'].items()):
            verified = rows[0]
            break
    if verified is None:
        raise ApiError('FEISHU_WRITE_READBACK_MISMATCH')
    result = {'record_id': record_id, 'asin': product['asin'], 'status': 'verified',
              'fields': patch['fields'], 'skipped': patch['skipped'], 'after': verified}
    write_json(client.output / ('after-' + record_id + '.json'), result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inspect', 'apply-one'))
    parser.add_argument('--url', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cli')
    parser.add_argument('--before', type=Path)
    parser.add_argument('--products', type=Path)
    parser.add_argument('--record-id')
    args = parser.parse_args()
    try:
        client = FeishuClient(args.out, args.cli)
        target = client.resolve(args.url)
        schema = client.fields(target)
        write_json(args.out / 'target.json', target)
        write_json(args.out / 'fields.json', schema)
        if args.command == 'inspect':
            rows = client.records(target, schema)
            write_json(args.out / 'records.json', rows)
            plan = create_plan(rows, args.url, columns=[field['name'] for field in schema])
            write_json(args.out / 'plan.json', plan)
            print(json.dumps({'status': 'ok', 'records': len(rows), 'fields': len(schema),
                              'new_rows': len(plan['new_rows']), 'update_rows': len(plan['update_rows'])}))
        else:
            if not args.before or not args.products or not args.record_id:
                raise ApiError('FEISHU_APPLY_INPUT_REQUIRED')
            text = args.before.read_text(encoding='utf-8-sig')
            try:
                rows = json.loads(text)
            except ValueError:
                rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ApiError('INVALID_FEISHU_BEFORE_RECORDS')
            before = next((row for row in rows if row.get('record_id') == args.record_id), None)
            if before is None:
                raise ApiError('FEISHU_RECORD_NOT_FOUND_IN_PLAN')
            products = json.loads(args.products.read_text(encoding='utf-8-sig'))
            if products['window']['basis'] != 'seven_calendar_dates_including_end_date':
                raise ApiError('FEISHU_WRITE_REQUIRES_SEVEN_DAY_WINDOW')
            matched = [row for row in products['products'] if row['asin'] == cell_text(before.get('ASIN'))]
            if len(matched) != 1:
                raise ApiError('FEISHU_PRODUCT_IDENTITY_MISMATCH')
            result = apply_one(client, target, schema, before, matched[0])
            print(json.dumps({key: result[key] for key in ('status', 'record_id', 'asin', 'fields', 'skipped')}, ensure_ascii=False))
        return 0
    except ApiError as error:
        print(json.dumps({'status': 'failed', 'category': error.code}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'status': 'failed', 'category': 'INVALID_FEISHU_LOCAL_INPUT_OR_RESPONSE'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
