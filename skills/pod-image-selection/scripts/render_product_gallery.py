#!/usr/bin/env python3
"""Render ranked-products.json as a local, multi-select image gallery."""
import argparse
import html
import json
from pathlib import Path
from urllib.parse import urlsplit


def render(document):
    esc = lambda value: html.escape(str(value), quote=True)
    products = document['products']
    cards = []
    for item in products:
        for field in ['image_url', 'product_url']:
            u = urlsplit(item[field])
            if u.scheme != 'https' or not u.hostname or u.username or u.password:
                raise ValueError('Gallery URLs must use HTTPS without credentials')
        title = html.unescape(item.get('title') or item['asin'])
        cards.append(f'''<article><label><span class="pick"><input type="checkbox" value="{esc(item['rank'])}"> <b>#{esc(item['rank'])}</b></span>
<img src="{esc(item['image_url'])}" alt="{esc(title)}" loading="lazy" referrerpolicy="no-referrer">
<h2>{esc(title)}</h2><p class="sales">父体近30天预估销量：{esc(format(item['parent_units_30d'], ',g'))}</p>
<p class="asin">ASIN：{esc(item['asin'])}<br>父体：{esc(item.get('parent_asin') or '未知')}</p></label>
<a href="{esc(item['product_url'])}" target="_blank" rel="noopener noreferrer">查看 Amazon 商品 ↗</a></article>''')
    mapping = {'query': document['query'], 'marketplace': document['marketplace'],
               'queried_at': document.get('queried_at'), 'source': document.get('source', 'sellersprite_extension_keyword_api'),
               'ranking_scope': document.get('ranking_scope', document.get('scope')),
               'products': [{'rank': p['rank'], 'asin': p['asin'], 'parent_asin': p.get('parent_asin')} for p in products]}
    encoded = json.dumps(mapping, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    returned = document.get('returned_candidates', len(products))
    total = document.get('total_matches')
    scope = (f'本次返回 {returned} 款候选；以下展示其中父体销量前 {len(products)} 款。接口报告总匹配 {total:,} 款，全部匹配商品的全局排名尚未验证。'
             if document.get('ranking_scope') == 'returned_candidates_only' and isinstance(total, int)
             else f'本次取得 {returned} 款候选，按父体近30天销量展示前 {len(products)} 款；全部匹配商品的全局排名尚未验证。')
    provenance = '来源：卖家精灵插件；查询时间：' + str(document.get('queried_at') or '未知')
    if document.get('sales_precision') == 'plugin_display':
        provenance += '。销量采用插件界面显示值，可能经过缩写或四舍五入。'
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>POD 竞品主图选择</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#17232e;font:15px/1.5 system-ui,-apple-system,sans-serif}
header{padding:26px max(24px,calc((100vw - 1440px)/2));background:white;border-bottom:1px solid #dce2e7}h1{font-size:26px;margin:0 0 8px}header p{margin:6px 0;color:#53616d}.toolbar{position:sticky;top:0;z-index:2;background:#ffffffef;backdrop-filter:blur(8px);border-bottom:1px solid #dce2e7;padding:12px 24px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}button{border:1px solid #b7c5d1;border-radius:7px;background:white;padding:8px 14px;font:inherit;cursor:pointer}button.primary{background:#125b75;border-color:#125b75;color:white}button:disabled{opacity:.45;cursor:default}#selection{flex:1;min-width:240px}.grid{max-width:1488px;margin:auto;padding:24px;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:18px}article{background:white;border:1px solid #dce2e7;border-radius:10px;overflow:hidden;padding:12px;display:flex;flex-direction:column}article:has(input:checked){outline:3px solid #1988aa}label{cursor:pointer}.pick{display:flex;gap:7px;align-items:center;margin-bottom:8px}input{width:20px;height:20px;accent-color:#125b75}img{width:100%;height:210px;object-fit:contain;background:white}h2{font-size:14px;line-height:1.45;height:61px;overflow:hidden;margin:12px 0 8px}.sales{font-weight:650;color:#125b75;font-size:13px;margin:7px 0}.asin{font-size:11px;color:#63717c;word-break:break-all}a{margin-top:auto;color:#125b75;font-size:13px;text-decoration:none}a:hover{text-decoration:underline}@media(max-width:1100px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:650px){.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:12px}img{height:170px}h1{font-size:22px}}
</style><header><h1>''' + esc(document['query']) + ''' · 美国站主图选择</h1><p>''' + esc(scope) + '''</p><p>''' + esc(provenance) + '''</p><p>可多选参考方向。父体总销量不代表主图中具体变体或图案的销量；关键词匹配也可能包含非目标品类。</p></header>
<div class="toolbar"><span id="selection" aria-live="polite">已选 0 张 · 勾选图片后可回复编号</span><button id="clear" type="button">清空选择</button><button class="primary" id="save" type="button" disabled>保存选择 JSON</button></div>
<main class="grid">''' + '\n'.join(cards) + '''</main><script>
const data = ''' + encoded + ''';
const boxes = [...document.querySelectorAll('input[type=checkbox]')];
function chosen(){const ranks=new Set(boxes.filter(b=>b.checked).map(b=>Number(b.value)));return data.products.filter(p=>ranks.has(p.rank));}
function update(){const selected=chosen();document.getElementById('selection').textContent=`已选 ${selected.length} 张`+(selected.length ? ' · 可回复编号：'+selected.map(p=>p.rank).join('、') : ' · 勾选图片后可回复编号');document.getElementById('save').disabled=!selected.length;}
boxes.forEach(b=>b.addEventListener('change',update));
document.getElementById('clear').addEventListener('click',()=>{boxes.forEach(b=>b.checked=false);update();});
document.getElementById('save').addEventListener('click',()=>{const result={query:data.query,marketplace:data.marketplace,queried_at:data.queried_at,source:data.source,ranking_scope:data.ranking_scope,selected_products:chosen()};const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='selected-products.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
</script></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ranked', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or args.ranked.parent / 'products.html'
    if output.resolve() == args.ranked.resolve():
        parser.error('Output must differ from input')
    document = json.loads(args.ranked.read_text(encoding='utf-8'))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(document), encoding='utf-8')
    print(json.dumps({'gallery': str(output), 'products': len(document['products'])}))


if __name__ == '__main__':
    main()
