#!/usr/bin/env python3
"""Huaban read-only URL, HTTP, normalization and gallery helpers; Python 3.9+."""

import argparse
import datetime as dt
import html
import json
from pathlib import Path
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

API = 'https://huaban.com/v3'
MAX_BYTES = 12 * 1024 * 1024
API_PATH = re.compile(r'/v3/(?:search/file|pins/\d+(?:/(?:similar|relatedboardsweb|relatedboardsweb-preview))?)')


class AccessError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def positive_id(value):
    if not re.fullmatch(r'[1-9]\d*', str(value or '')):
        raise ValueError('需要有效的正整数 ID')
    return str(value)


def api_url(kind, query='', pin_id=None, limit=20, page=1, preview=False, max_id=None):
    if not 1 <= limit <= 40 or page < 1:
        raise ValueError('limit 应为 1–40，page 应为正整数')
    if kind == 'search':
        if not query.strip():
            raise ValueError('搜索关键词不能为空')
        path = '/search/file'
        params = dict(text=query, sort='all', limit=limit, page=page,
                      position='search_pin', relations='false', all_match='true')
    else:
        pin_id = positive_id(pin_id)
        path = '/pins/' + pin_id
        if kind == 'detail':
            params = {}
        elif kind == 'similar':
            path += '/similar'
            params = dict(limit=limit)
            if max_id:
                params['max'] = positive_id(max_id)
        elif kind == 'related-boards':
            path += '/relatedboardsweb-preview' if preview else '/relatedboardsweb'
            params = {} if preview else dict(limit=limit, page=page, pins_limit=3)
        else:
            raise ValueError('未知接口类型')
    return API + path + ('?' + urllib.parse.urlencode(params) if params else '')


def safe_url(value, image=False):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return ''
    try:
        u = urllib.parse.urlsplit(value)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
            return ''
        if image and (u.scheme != 'https' or not u.hostname.endswith('.huaban.com')
                      or u.port not in (None, 443)):
            return ''
    except ValueError:
        return ''
    return value


def validate_api_url(url):
    u = urllib.parse.urlsplit(url)
    if (u.scheme != 'https' or u.netloc != 'huaban.com' or u.fragment
            or not API_PATH.fullmatch(u.path)):
        raise ValueError('仅支持已观察的 huaban.com/v3 只读接口')


def request_bytes(url, headers):
    try:
        req = urllib.request.Request(url, headers=headers, method='GET')
        with urllib.request.build_opener(NoRedirect).open(req, timeout=25) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise AccessError('响应过大；缩小采集数量')
            return response.status, response.headers.get('Content-Type', ''), body
    except urllib.error.HTTPError as error:
        # Never echo request headers, URL query values, server body, or credentials.
        raise AccessError('HTTP %s；本次请求未成功，请切换浏览器或检查会话' % error.code) from None
    except (urllib.error.URLError, TimeoutError):
        raise AccessError('网络连接失败；未进行自动重试') from None


def parse_api_response(status, content_type, body):
    if status != 200:
        raise AccessError('非成功 HTTP 状态')
    if 'json' not in content_type.lower():
        raise AccessError('不是 JSON，可能是登录或浏览器校验页面')
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        raise AccessError('JSON 无法解析') from None
    if not isinstance(data, dict) or data.get('error') or data.get('errors'):
        raise AccessError('接口返回业务错误或未知结构')
    expected = any(isinstance(data.get(k), list) for k in ('pins', 'boards'))
    expected = expected or (isinstance(data.get('pin'), dict) and bool(data['pin'].get('pin_id')))
    if not expected:
        raise AccessError('JSON 中缺少 pins、boards 或 pin，不是成功候选响应')
    if data.get('success') is False or data.get('code') not in (None, 0, 200, '0', '200'):
        raise AccessError('接口业务状态未成功')
    return data


def load_headers(headers_file=None, cookie_file=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0',
               'Referer': 'https://huaban.com/'}
    if headers_file:
        supplied = read_json(headers_file)
        if not isinstance(supplied, dict):
            raise ValueError('请求头文件必须是 JSON 对象')
        for name, value in supplied.items():
            if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9-]+', name)
                    or any(c in value for c in '\r\n')
                    or name.lower() in ('host', 'connection', 'content-length', 'accept-encoding')):
                raise ValueError('请求头文件含不支持的字段或换行')
            headers[name] = value
    if cookie_file:
        cookie = Path(cookie_file).read_text(encoding='utf-8').strip()
        if cookie.lower().startswith('cookie:'):
            cookie = cookie.split(':', 1)[1].strip()
        if not cookie or '\r' in cookie or '\n' in cookie:
            raise ValueError('Cookie 文件应为单行 Cookie header 文本')
        headers['Cookie'] = cookie
    return headers


def walk_pins(data, board=None):
    if isinstance(data, list):
        for value in data:
            yield from walk_pins(value, board)
    elif isinstance(data, dict):
        if data.get('pin_id'):
            yield data, board
            return
        for key in ('pins', 'candidates', 'pin', 'items', 'data'):
            if key in data:
                yield from walk_pins(data[key], board)
        if isinstance(data.get('board'), dict):
            yield from walk_pins(data['board'].get('pins', []), data['board'])
        for value in data.get('boards', []):
            if isinstance(value, dict):
                yield from walk_pins(value.get('pins', []), value)


def file_key_from_url(url):
    # Identity only; never use the result to construct a download URL.
    path = urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1]
    return re.sub(r'_(?:fw\d+(?:webp)?|sq\d+(?:webp)?)$', '', path)


def append_unique(target, values):
    for value in values:
        if value not in target:
            target.append(value)


def normalize(documents, via='unknown', query='', style='', parent_pin_id=None):
    candidates, aliases, dropped = [], {}, set()
    total_seen = 0
    for document in documents:
        for pin, board in walk_pins(document):
            if pin.get('is_private') in (1, '1', True):
                continue
            total_seen += 1
            pin_id = positive_id(pin['pin_id'])
            file = pin.get('file') if isinstance(pin.get('file'), dict) else {}
            image_url = safe_url(pin.get('image_url') or file.get('url'), image=True)
            file_key = pin.get('file_key') or file.get('key') or file_key_from_url(image_url)
            file_id = pin.get('file_id')
            board_id = pin.get('board_id') or (board or {}).get('board_id')
            path = {'via': via}
            if query:
                path['query'] = query
            if parent_pin_id:
                path['parent_pin_id'] = str(parent_pin_id)
            if board:
                path['parent_board_id'] = str(board['board_id'])
            paths = list(pin.get('source_paths') or [])
            append_unique(paths, [path])
            item = dict(pin_id=pin_id, pin_ids=[pin_id],
                        pin_url='https://huaban.com/pins/' + pin_id,
                        image_url=image_url or None, file_id=file_id, file_key=file_key or None,
                        width=pin.get('width', file.get('width')),
                        height=pin.get('height', file.get('height')),
                        dimension_source=pin.get('dimension_source') or 'api_file_metadata',
                        title=pin.get('title') or pin.get('raw_text') or '未命名采集',
                        source_url=safe_url(pin.get('source_url') or pin.get('link')) or None,
                        board_id=board_id, board_title=(board or {}).get('title'),
                        source_paths=paths, decision=pin.get('decision') or '待看图',
                        visual_notes=pin.get('visual_notes') or '',
                        product_fit=pin.get('product_fit') or '',
                        limitations=pin.get('limitations') or '',
                        rights_status=pin.get('rights_status') or '灵感参考，授权待核实')
            for key in ('thumbnail_width', 'thumbnail_height', 'file_material', 'local_image'):
                if key in pin:
                    item[key] = pin[key]
            append_unique(item['pin_ids'], pin.get('pin_ids') or [])
            keys = ['pin:' + pin_id]
            if file_id:
                keys.append('file:' + str(file_id))
            if file_key:
                keys.append('key:' + file_key)
            hits = sorted({aliases[k] for k in keys if k in aliases})
            if not hits:
                index = len(candidates)
                candidates.append(item)
            else:
                index = hits[0]
                target = candidates[index]
                for other in hits[1:]:
                    append_unique(target['pin_ids'], candidates[other]['pin_ids'])
                    append_unique(target['source_paths'], candidates[other]['source_paths'])
                    dropped.add(other)
                    for key, old_index in list(aliases.items()):
                        if old_index == other:
                            aliases[key] = index
                append_unique(target['pin_ids'], item['pin_ids'])
                append_unique(target['source_paths'], item['source_paths'])
                # API dimensions can enrich a DOM thumbnail record without inventing resolution.
                if target['dimension_source'] == 'dom_thumbnail' and file.get('width'):
                    target.update(width=file.get('width'), height=file.get('height'),
                                  dimension_source='api_file_metadata')
                for field in ('image_url', 'file_id', 'file_key'):
                    if not target.get(field) and item.get(field):
                        target[field] = item[field]
            for key in keys:
                aliases[key] = index
    result = [value for index, value in enumerate(candidates) if index not in dropped]
    return dict(collected_at=dt.datetime.now(dt.timezone.utc).isoformat(), query=query, style=style,
                seen=total_seen, duplicates=total_seen - len(result), candidates=result)


def download_image(url, path):
    if not safe_url(url, image=True):
        raise AccessError('不是花瓣 HTTPS 图片地址')
    status, kind, body = request_bytes(url, {'Referer': 'https://huaban.com/', 'User-Agent': 'Mozilla/5.0'})
    if status != 200 or not kind.lower().startswith('image/'):
        raise AccessError('没有得到成功的图片响应')
    ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp',
           'image/gif': '.gif', 'image/avif': '.avif'}.get(kind.split(';')[0].lower())
    if not ext:
        raise AccessError('不支持此图片格式')
    path = path.with_suffix(ext)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def render_gallery(document, output, download=False, selected_only=False):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cards, failures, downloaded = [], 0, 0
    items = document.get('candidates', [])
    if selected_only:
        items = [x for x in items if x.get('decision') == '保留']
    else:
        items = [x for x in items if x.get('decision') != '排除']
    escape = lambda value: html.escape(str(value or ''), quote=True)
    for number, pin in enumerate(items, 1):
        url = safe_url(pin.get('image_url'), image=True)
        local_image = pin.get('local_image')
        if local_image:
            source = Path(local_image).expanduser().resolve()
            if (not source.is_file() or source.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif')
                    or source.stat().st_size > MAX_BYTES):
                raise ValueError('本地预览图片不可用')
            target = output.parent / (output.stem + '_assets') / ('%03d' % number + source.suffix.lower())
            target.parent.mkdir(parents=True, exist_ok=True)
            if source != target.resolve():
                shutil.copyfile(source, target)
            url = urllib.parse.quote(target.relative_to(output.parent).as_posix())
            downloaded += 1
        elif download and url:
            try:
                image_path = download_image(url, output.parent / (output.stem + '_assets') / ('%03d' % number))
                url = urllib.parse.quote(image_path.relative_to(output.parent).as_posix())
                downloaded += 1
            except AccessError:
                failures += 1
        dimensions = 'API 文件尺寸：%s × %s px（当前展示预览）' % (pin['width'], pin['height']) if pin.get('width') and pin.get('height') else '尺寸未核实'
        if pin.get('dimension_source') == 'dom_thumbnail':
            dimensions = '仅取得缩略图，原图尺寸未核实'
        image_tag = ('<img loading="lazy" src="%s" alt="%s">' % (escape(url), escape(pin.get('title')))
                     if url else '<p class="empty">未取得预览，请查看原采集</p>')
        links = []
        for field, label in [('pin_url', '花瓣采集'), ('source_url', '原站线索')]:
            href = safe_url(pin.get(field))
            if href:
                links.append('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (escape(href), label))
        notes = ''.join('<p><b>%s</b>%s</p>' % (label, escape(pin.get(field)))
                        for field, label in [('visual_notes', '风格：'), ('product_fit', '方向：'), ('limitations', '限制：')]
                        if pin.get(field))
        cards.append('<article>%s<div class="body"><span class="badge">%02d · %s</span><h2>%s</h2><p class="meta">%s</p>%s<p class="rights">%s</p><nav>%s</nav></div></article>' %
                     (image_tag, number, escape(pin.get('decision', '待看图')), escape(pin.get('title')), escape(dimensions), notes, escape(pin.get('rights_status', '授权待核实')), ' · '.join(links)))
    title = document.get('title') or 'POD 花瓣素材候选'
    css = 'body{margin:0;background:#f3f2ed;color:#202b2a;font:16px/1.55 system-ui,sans-serif}header,main{max-width:1400px;margin:auto;padding:28px}h1{margin:0;font-size:30px}header p{margin:8px 0}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;padding-top:0}article{background:white;border:1px solid #dadfd9;border-radius:14px;overflow:hidden}img{display:block;width:100%;height:340px;object-fit:contain;background:#e9e8e1}.body{padding:18px}h2{font-size:17px;margin:10px 0}.badge{font-size:13px;color:#1c5f51}.meta,.rights{font-size:13px;color:#59635d}a{color:#175b50}a:focus-visible{outline:3px solid #c78720}.empty{height:280px;padding:20px}nav{margin-top:15px}b{font-weight:600}@media print{article{break-inside:avoid}header,main{padding:14px}main{grid-template-columns:repeat(3,1fr)}img{height:230px}}'
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>%s</title><style>%s</style><header><h1>%s</h1><p>关键词：%s　风格：%s　共 %d 张</p><p>初筛参考图；印刷原文件、无缝效果及 POD 授权按各图证据核实。外链预览过期可回到采集刷新。</p></header><main>%s</main></html>' % (escape(title), css, escape(title), escape(document.get('query')), escape(document.get('style')), len(items), ''.join(cards))
    output.write_text(page, encoding='utf-8')
    return {'cards': len(items), 'downloaded': downloaded, 'download_failed': failures}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('api-url', help='只生成已观察接口的 URL，不联网')
    p.add_argument('kind', choices=['search', 'detail', 'similar', 'related-boards'])
    p.add_argument('--query', default='')
    p.add_argument('--pin-id')
    p.add_argument('--limit', type=int, default=20)
    p.add_argument('--page', type=int, default=1)
    p.add_argument('--max-id')
    p.add_argument('--preview', action='store_true')
    p = sub.add_parser('fetch', help='请求一次；失败则返回非零，无自动重试')
    p.add_argument('--url', required=True)
    p.add_argument('--headers-file')
    p.add_argument('--cookie-file')
    p.add_argument('--output', required=True)
    p = sub.add_parser('normalize', help='合并 API 或 DOM 导出，保留来源并去重')
    p.add_argument('inputs', nargs='+')
    p.add_argument('--via', default='unknown')
    p.add_argument('--query', default='')
    p.add_argument('--style', default='')
    p.add_argument('--parent-pin-id')
    p.add_argument('--output', required=True)
    p = sub.add_parser('gallery', help='渲染带来源链接的 HTML 图片候选集')
    p.add_argument('input')
    p.add_argument('--output', required=True)
    p.add_argument('--download', action='store_true')
    p.add_argument('--selected-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'api-url':
            print(api_url(args.kind, args.query, args.pin_id, args.limit, args.page, args.preview, args.max_id))
        elif args.command == 'fetch':
            validate_api_url(args.url)
            headers = load_headers(args.headers_file, args.cookie_file)
            status, kind, body = request_bytes(args.url, headers)
            data = parse_api_response(status, kind, body)
            write_json(args.output, data)
            print(json.dumps({'status': 'json_ok', 'output': args.output}, ensure_ascii=False))
        elif args.command == 'normalize':
            data = normalize([read_json(x) for x in args.inputs], args.via, args.query, args.style, args.parent_pin_id)
            write_json(args.output, data)
            print(json.dumps({'candidates': len(data['candidates']), 'duplicates': data['duplicates']}, ensure_ascii=False))
        else:
            print(json.dumps(render_gallery(read_json(args.input), args.output, args.download, args.selected_only)))
    except AccessError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (ValueError, OSError):
        # Input exceptions can contain secrets; print no raw exception or input.
        print('输入文件或参数无效；请检查路径、JSON 结构和命令参数', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
