import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/amazon-jp-competitor-tracking/scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    from feishu_plan import create_plan
    from feishu_cli import build_patch, apply_one
    from competitor_core import ApiError, CHANGE_SCHEMA, declared_package_quantity
    from competitor_changes import normalize_changes
    from competitor_core import window
finally:
    sys.path.pop(0)

URL = 'https://tenant.feishu.cn/wiki/Node123?table=tblDemo&view=vewDemo'
ASIN = 'B07RPLXMXD'
SCHEMA = [
    {'id': 'fldTitle', 'name': '标题', 'type': 'text'},
    {'id': 'fldBrand', 'name': '品牌', 'type': 'text'},
    {'id': 'fldMedia', 'name': '图片变化', 'type': 'select', 'options': [{'name': '是'}, {'name': '否'}]},
    {'id': 'fldStock', 'name': '库存状态（是/否）断货', 'type': 'select', 'options': [{'name': '是'}, {'name': '否'}]},
    {'id': 'fldPrice', 'name': '当前售价', 'type': 'text'},
    {'id': 'fldFluctuation', 'name': '价格波动（超10%）', 'type': 'select', 'options': [{'name': '是'}, {'name': '否'}]},
    {'id': 'fldQuantity', 'name': '数量装', 'type': 'number'},
    {'id': 'fldAsin', 'name': 'ASIN', 'type': 'text'},
    {'id': 'fldGroup', 'name': '分组', 'type': 'text'},
    {'id': 'fldTime', 'name': '抓取时间', 'type': 'updated_at'},
]


def product(marker=False):
    return {'asin': ASIN, 'title': '互換フィルター 2個入り', 'brand': 'Fixture', 'price': 1852,
            'media_change_observed': marker, 'history': {'schema': CHANGE_SCHEMA, 'status': 'ok'},
            'stock': 'in_stock', 'package_quantity': 2}


class FakeClient:
    def __init__(self, output, rows):
        self.output, self.rows, self.writes = Path(output), copy.deepcopy(rows), []

    def records(self, target, schema, record_ids):
        return [copy.deepcopy(row) for row in self.rows if row['record_id'] in record_ids]

    def update(self, target, updates):
        self.writes.append(copy.deepcopy(updates))
        names = {field['id']: field['name'] for field in SCHEMA}
        for row in self.rows:
            for key, value in updates.get(row['record_id'], {}).items():
                row[names[key]] = value


class FeishuTests(unittest.TestCase):
    def test_plan_uses_empty_title_and_preserves_distinct_record_ids(self):
        rows = [{'record_id': 'recOld', 'ASIN': ASIN, '标题': '旧标题'},
                {'record_id': 'recNew', 'ASIN': ASIN, '标题': ' \n'},
                {'record_id': 'recSecond', 'ASIN': 'B07WQGD7G1', '标题': None}]
        plan = create_plan(rows, URL, columns=['ASIN', '标题'])
        self.assertEqual([row['record_id'] for row in plan['new_rows']], ['recNew', 'recSecond'])
        self.assertEqual([row['record_id'] for row in plan['update_rows']], ['recOld'])
        self.assertEqual(plan['update_fetch_asins'], [])
        self.assertEqual(plan['phase_order'], ['new_rows', 'update_rows'])
        self.assertEqual(plan['write_operation'], 'update_existing_records_only')

    def test_missing_title_column_cannot_turn_entire_table_into_new_records(self):
        with self.assertRaisesRegex(ApiError, 'FEISHU_REQUIRED_COLUMNS'):
            create_plan([{'record_id': 'recNew', 'ASIN': ASIN}], URL, columns=['ASIN'])

    def test_conflicting_asin_and_link_are_skipped_and_duplicate_record_ids_stop(self):
        row = {'record_id': 'recNew', 'ASIN': ASIN, '链接': 'https://www.amazon.co.jp/dp/B07WQGD7G1'}
        plan = create_plan([row], URL, columns=['ASIN', '标题', '链接'])
        self.assertEqual(plan['new_rows'], [])
        self.assertEqual(plan['invalid_rows'][0]['code'], 'FEISHU_ASIN_LINK_MISMATCH')
        with self.assertRaisesRegex(ApiError, 'DUPLICATE_FEISHU_RECORD_ID'):
            create_plan([row, row], URL, columns=['ASIN', '标题'])

    def test_media_yes_no_unknown_and_outdated_schema_are_distinct(self):
        before = {'ASIN': ASIN, '标题': None}
        self.assertEqual(build_patch(product(True), before, SCHEMA)['fields']['图片变化'], ['是'])
        self.assertEqual(build_patch(product(False), before, SCHEMA)['fields']['图片变化'], ['否'])
        self.assertNotIn('图片变化', build_patch(product(None), before, SCHEMA)['fields'])
        old = product(False)
        old['history']['schema'] = 'obsolete-schema'
        self.assertNotIn('图片变化', build_patch(old, before, SCHEMA)['fields'])
        old = product(False)
        old['history']['status'] = 'unavailable'
        self.assertNotIn('图片变化', build_patch(old, before, SCHEMA)['fields'])

    def test_stock_false_is_in_stock_and_readonly_manual_fields_are_not_written(self):
        patch = build_patch(product(False), {'ASIN': ASIN}, SCHEMA)['fields']
        self.assertEqual(patch['库存状态（是/否）断货'], ['否'])
        self.assertEqual(patch['数量装'], 2)
        for name in ('抓取时间', '分组', 'ASIN'):
            self.assertNotIn(name, patch)

    def test_raw_login_mask_stops_writeback_instead_of_replacing_real_cells(self):
        for field in ('title', 'brand', 'price', 'child_monthly_purchased'):
            data = product()
            data[field] = '****'
            with self.subTest(field=field), self.assertRaisesRegex(ApiError, 'FEISHU_SOURCE_REQUIRES_LOGIN'):
                build_patch(data, {'ASIN': ASIN, '当前售价': '￥2,204'}, SCHEMA)

    def test_price_change_has_strict_ten_percent_boundary_and_needs_a_baseline(self):
        data = product()
        data['price'] = 110
        before = {'ASIN': ASIN, '当前售价': '￥100'}
        self.assertEqual(build_patch(data, before, SCHEMA)['fields']['价格波动（超10%）'], ['否'])
        data['price'] = 111
        self.assertEqual(build_patch(data, before, SCHEMA)['fields']['价格波动（超10%）'], ['是'])
        self.assertNotIn('价格波动（超10%）', build_patch(data, {'ASIN': ASIN}, SCHEMA)['fields'])

    def test_record_id_write_and_readback_do_not_modify_other_rows_with_same_asin(self):
        before = {'record_id': 'recNew', 'ASIN': ASIN, '标题': None, '分组': 'keep'}
        another = {'record_id': 'recOther', 'ASIN': ASIN, '标题': '不可改'}
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(tmp, [before, another])
            result = apply_one(client, {}, SCHEMA, before, product())
            self.assertEqual(result['status'], 'verified')
            self.assertEqual(set(client.writes[0]), {'recNew'})
            self.assertEqual(client.rows[1], another)
            self.assertEqual(client.rows[0]['分组'], 'keep')

    def test_concurrent_edit_stops_before_any_remote_write(self):
        before = {'record_id': 'recNew', 'ASIN': ASIN, '标题': None}
        with tempfile.TemporaryDirectory() as tmp:
            client = FakeClient(tmp, [dict(before, 标题='用户刚修改')])
            with self.assertRaisesRegex(ApiError, 'FEISHU_RECORD_CHANGED_SINCE_READ'):
                apply_one(client, {}, SCHEMA, before, product())
            self.assertEqual(client.writes, [])

    def test_gallery_marker_ignores_other_types_and_outside_week_but_keeps_positive(self):
        scope = window('2026-11-24')
        rows = [{'changeType': n, 'timePoint': '2026-11-20 00:00:00', 'ignored_details': 'discard'}
                for n in range(8)]
        rows.append({'changeType': 2, 'timePoint': '2026-11-17 23:59:59'})
        result = normalize_changes(rows, scope)
        self.assertTrue(result['media_change_observed'])
        self.assertEqual([row['changeType'] for row in result['events']], [2, 4, 6, 7])
        self.assertTrue(all('ignored_details' not in row for row in result['events']))
        self.assertFalse(normalize_changes([{'changeType': 1, 'timePoint': '2026-11-20'}], scope)['media_change_observed'])
        self.assertIsNone(normalize_changes([{'changeType': 8, 'timePoint': '2026-11-20'}], scope)['media_change_observed'])

    def test_package_quantity_does_not_guess_mixed_components_or_model_numbers(self):
        self.assertEqual(declared_package_quantity('V10 2個入り'), 2)
        self.assertIsNone(declared_package_quantity('V10 SV12 969082-02'))
        self.assertIsNone(declared_package_quantity('フィルター2個とブラシ1本'))


if __name__ == '__main__':
    unittest.main()
