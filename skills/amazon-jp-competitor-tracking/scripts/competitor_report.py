"""CSV/HTML/JSON presentation for product snapshots and gallery change markers."""
import csv
import datetime as dt
import html
import json

from competitor_core import JST, CHANGE_SCHEMA, BROWSER_OFFER_SOURCE, safe_image, write_json

COLUMNS = [('asin', 'ASIN'), ('url', '商品链接'), ('title', '标题'), ('brand', '品牌'),
           ('delivery_status', '配送目的地'),
           ('main_image', '主图链接'), ('parent_asin', '父ASIN'),
           ('root_rank', '大类排名'), ('root_category', '大类名称'), ('sub_ranks', '小类排名'),
           ('parent_sales_30d', '父体近30天估算销量'), ('child_monthly_purchased', '子体近30天购买量阈值'),
           ('price', '售价'), ('currency', '币种'), ('rating', '评分'), ('review_count', '评论数'),
           ('discount_percent', '当前折扣百分比'), ('reference_price', '当前参考价'),
           ('buy_box', '当前购物车状态'), ('stock', '当前库存状态'),
           ('media_change_observed', '近7天图片/视频变化'),
           ('status', '采集状态'), ('missing_fields', '未取得字段'),
           ('collected_at', '抓取时间')]


def cell(value):
    if value is None:
        return '未知'
    if type(value) is bool:
        return '有观测记录' if value else '未观测到（来源覆盖未知）'
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return str(value)


def display_value(key, row):
    value = row.get(key)
    if value is None:
        return '未知'
    if key == 'buy_box':
        return '有购物车' if value else '无可用购物车'
    if key == 'stock':
        return {'in_stock': '有货', 'out_of_stock': '缺货'}.get(value, value)
    if key == 'sub_ranks':
        return '；'.join(f'{r.get("label") or "类别未返回"}：{cell(r.get("rank"))}' for r in value) or '未返回小类排名'
    if key == 'missing_fields':
        labels = dict(COLUMNS)
        return '、'.join(labels.get(field, field) for field in value) or '无'
    if key == 'media_change_observed':
        return '是' if value else '否'
    if key == 'status':
        return {'ok': '所需字段已取得', 'partial': '部分字段已取得', 'failed': '全部来源失败'}.get(value, value)
    if key == 'collected_at':
        try:
            return dt.datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M:%S %z')
        except ValueError:
            pass
    return cell(value)


def save_listing_changes(output, results, requested, scope, stopped):
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'changes.json', {'format': CHANGE_SCHEMA, 'marketplace': 'JP',
        'requested_asins': requested, 'window': scope, 'collection_method': 'independent_http',
        'provider_coverage': 'not_reported', 'stop_reason': stopped, 'products': results})
    prefix = '全部周期' if scope['basis'] == 'all_available_history_through_end' else '近7天'
    labels = ('ASIN', prefix + '图片/视频变化', '接口状态')
    values = [(row['asin'], display_value('media_change_observed', row), row['history_status']) for row in results]
    with (output / 'changes.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(labels)
        writer.writerows(values)
    esc = lambda value: html.escape(str(value), quote=True)
    rows = ['<tr>' + ''.join('<td>' + esc(value) + '</td>' for value in row) + '</tr>' for row in values]
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>图片/视频变化</title><style>body{font:15px system-ui;margin:32px;color:#17324d}table{border-collapse:collapse;width:100%}th,td{border:1px solid #dde5ee;padding:12px;text-align:left;overflow-wrap:anywhere}</style>'
    page += f'<h1>{prefix}图片/视频变化</h1><p>观察区间：{esc(scope["start"])} 至 {esc(scope["end"])}。</p>'
    page += '<p>采用卖家精灵 Keepa 的“修改图片/视频”合并图标口径。有标记为是，接口成功且未观测到标记为否；失败或记录异常为未知。来源完整覆盖未声明。</p>'
    page += '<table><thead><tr>' + ''.join('<th>' + esc(label) + '</th>' for label in labels) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
    errors = [{'asin': row['asin'], 'errors': row['errors']} for row in results if row['errors']]
    if errors:
        page += '<p>接口异常：</p><pre>' + esc(json.dumps(errors, ensure_ascii=False, indent=2)) + '</pre>'
    (output / 'report.html').write_text(page + '</html>', encoding='utf-8')


def save_report(output, results, requested, scope, stopped):
    output.mkdir(parents=True, exist_ok=True)
    browser_offers = sum(bool(row.get('offer_observed_at')) for row in results)
    payload = {'format': 'amazon-jp-competitor-tracking-v1', 'marketplace': 'JP', 'window': scope,
               'requested_asins': requested, 'collected_count': len(results), 'stop_reason': stopped,
               'collection_method': 'http_with_browser_offers' if browser_offers else 'independent_http', 'products': results}
    write_json(output / 'products.json', payload)
    with (output / 'products.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow([label for _, label in COLUMNS])
        for row in results:
            values = [display_value(key, row) for key, _ in COLUMNS]
            writer.writerow(["'" + v if v.lstrip().startswith(('=', '+', '-', '@')) else v for v in values])
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    for row in results:
        image = safe_image(row.get('main_image'))
        picture = f'<img src="{esc(image)}" alt="{esc(row["asin"])} 主图" loading="lazy">' if image else '<p>主图未取得</p>'
        fields = []
        for key, label in COLUMNS:
            if key in ('main_image', 'url'):
                continue
            value = display_value(key, row)
            if key == 'price' and row.get(key) is not None:
                source = row.get('field_sources', {}).get('price')
                if source == 'amazon_jp_verified_delivery.price':
                    value += '（Amazon 日本配送报价）'
                elif source == BROWSER_OFFER_SOURCE + '.price':
                    value += '（Amazon 浏览器商品页）'
                elif source in ('product.price', 'sales.price'):
                    value += '（卖家精灵快照）'
            if key == 'reference_price' and row.get(key) is not None and row.get('reference_price_label'):
                value += f'（{row["reference_price_label"]}）'
            if key == 'discount_percent' and row.get(key) is not None:
                value = f'{row[key]:g}%'
            fields.append(f'<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>')
        if row.get('offer_observed_at'):
            observed = dt.datetime.fromisoformat(row['offer_observed_at']).astimezone(JST)
            fields.append(f'<tr><th>Amazon 报价观察时间（日本）</th><td>{observed:%Y-%m-%d %H:%M:%S}</td></tr>')
        events = ''
        for event in row['history']['events']:
            events += '<li>' + esc(event['timePoint'] + '：修改图片/视频') + '</li>'
        errors = esc(json.dumps(row['errors'], ensure_ascii=False))
        cards.append(f'<article><h2><a href="{esc(row["url"])}">{esc(row["asin"])}</a></h2>{picture}<table>{"".join(fields)}</table>'
                     f'<details><summary>修改记录与接口状态</summary><ul>{events}</ul><pre>{errors}</pre></details></article>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>日本亚马逊竞对页面追踪</title><style>body{font:15px system-ui;margin:32px;color:#17324d;background:#f3f6fa}article{background:white;padding:24px;margin:20px 0;border-radius:12px}img{max-width:240px;max-height:240px;object-fit:contain;float:right}table{border-collapse:collapse;max-width:100%;width:850px}td,th{border-bottom:1px solid #dde5ee;padding:8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{min-width:170px}pre{white-space:pre-wrap}li{overflow-wrap:anywhere}details{clear:both;padding-top:16px}@media(max-width:700px){body{margin:12px}article{padding:12px}img{float:none}table{width:100%}}</style>'
    page += f'<h1>日本亚马逊竞对页面追踪</h1><p>已处理 {len(results)} / {len(requested)} 个 ASIN；观察日期 {esc(scope["start"][:10])} 至 {esc(scope["end"][:10])}（日本时间，含结束日）。</p>'
    verified = sum(bool(row.get('delivery_destination', {}).get('verified')) for row in results)
    page += f'<p>已确认配送至日本 {verified} / {len(results)} 个商品。参考价、折扣、购物车和库存来自 Amazon 商品页的日本配送报价。卖家精灵接口提供商品快照和销量，包含快照售价。父体销量为估算值，子体购买量按 100+ 等阈值展示。图片变化采用近七天“修改图片/视频”合并图标口径；有效空历史记为否，失败或异常为未知。</p>'
    page += '<p>修改时间保留接口 timePoint，来源时区未声明；00:00:00 不代表实际修改发生在零点。</p>'
    if browser_offers:
        page += f'<p>通过 Amazon 浏览器商品页补采了 {browser_offers} 个商品的报价。每项来源见 JSON，页面观察证据见 evidence 目录。</p>'
    reasons = {'AMAZON_HUMAN_VERIFICATION_REQUIRED': 'Amazon 请求触发验证页，未能完成配送至日本的设置与核验。',
               'AMAZON_DESTINATION_NOT_CONFIRMED': '未能核验刷新后的配送标签，当前报价字段未采用。',
               'AMAZON_DESTINATION_REJECTED': 'Amazon 未接受本次日本配送设置，当前报价字段未采用。'}
    stages = {'initial_product': '首次请求商品页', 'destination_setup': '设置日本配送',
              'product_refresh': '刷新商品页', 'destination_verification': '核对配送目的地',
              'offer_extraction': '读取商品报价'}
    for row in results:
        for error in row.get('errors', []):
            if error.get('source') == 'amazon':
                reason = reasons.get(error.get('code'), 'Amazon 配送或报价读取失败：' + str(error.get('code')))
                stage = stages.get(error.get('stage'))
                if error.get('not_attempted'):
                    reason = '本轮 Amazon 采集此前已停止，该商品的报价请求未发出。'
                elif stage and error.get('code') == 'AMAZON_HUMAN_VERIFICATION_REQUIRED':
                    reason = f'{stage}时，Amazon 返回了验证页，未取得该商品的日本配送报价。'
                elif stage:
                    reason = f'{stage}时遇到问题。{reason}'
                page += '<p role="status"><strong>' + esc(row['asin']) + '：</strong>' + esc(reason) + '</p>'
    if stopped:
        page += '<p>接口停止原因：' + esc(stopped) + '</p>'
    page += ''.join(cards) + '</html>'
    (output / 'report.html').write_text(page, encoding='utf-8')
