#!/usr/bin/env python3
"""Read observed PODQHY template/material APIs without exporting browser credentials."""
import argparse
import base64
import gzip
import json
import math
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.podqhy.com"
MAX_BYTES = 32 * 1024 * 1024
ENDPOINTS = {"flatList", "viewList", "color", "image"}


class AccessError(Exception):
    pass


def decode_response(body):
    if body.startswith(b"\x1f\x8b"):
        body = gzip.decompress(body)
    try:
        text = body.decode("utf-8").strip()
        if not text.startswith("{"):
            text = urllib.parse.unquote(base64.b64decode(text.replace("DoNnIeR", "D"),
                                                        validate=True).decode("utf-8"))
        result = json.loads(text)
    except (ValueError, UnicodeError) as exc:
        raise AccessError("接口返回登录页或非预期数据，请在可见浏览器中检查登录状态") from exc
    if not isinstance(result, dict) or not isinstance(result.get("data"), (dict, list)):
        raise AccessError("接口未返回有效 data，不能当作成功")
    if result.get("error") or any(isinstance(result.get(k), int) and result[k] >= 400
                                  for k in ("code", "status_code")):
        raise AccessError("接口返回错误状态，不能当作成功")
    return result


def request_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Referer": BASE + "/custom"})
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            if urllib.parse.urlsplit(response.url).path.startswith("/login"):
                raise AccessError("需要登录，请打开可见页面让用户完成登录")
            body = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise AccessError("接口 HTTP %s；停止请求，在浏览器检查会话或站点提示" % exc.code) from exc
    if len(body) > MAX_BYTES:
        raise AccessError("响应超过 32 MiB 上限")
    return body


def api_get(endpoint, params):
    if endpoint not in ENDPOINTS:
        raise ValueError("Unsupported endpoint")
    return decode_response(request_bytes(BASE + "/edit/" + endpoint + "?" +
                                         urllib.parse.urlencode(params)))


def write_json(path, doc):
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch_template(model_id, color_id, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    flats = api_get("flatList", {"modelId": model_id})
    if not isinstance(flats["data"], list) or not flats["data"]:
        raise AccessError("未取得设计面")
    views = api_get("viewList", {"modelId": model_id})
    if not isinstance(views["data"], list) or not views["data"]:
        raise AccessError("未取得效果图模板")
    color = api_get("color", {"colorId": color_id})
    for name, doc in (("flatList", flats), ("viewList", views), ("color", color)):
        write_json(out / (name + ".json"), doc)
    metadata = {"model_id": model_id, "color_id": color_id,
                "editor_url": BASE + "/edit?colorId=" + str(color_id),
                "flat_count": len(flats["data"]), "view_count": len(views["data"])}
    write_json(out / "template-info.json", metadata)
    return metadata


def material_params(code, template):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", code):
        raise ValueError("Invalid material code")
    flats = [f for f in template["data"] if f.get("config", {}).get("edit")]
    if not flats:
        raise ValueError("No editable surfaces")
    # Reproduce the observed getFlatMaxSize/getAdjustSize request parameters.
    widest = max(flats, key=lambda f: int(f["config"]["flatWidth"]))
    tallest = max(flats, key=lambda f: int(f["config"]["flatHeight"]))
    max_width, max_height = int(widest["config"]["flatWidth"]), int(tallest["config"]["flatHeight"])
    largest = widest if max_width > max_height else tallest
    config = largest["config"]
    width, height = int(config["flatWidth"]), int(config["flatHeight"])
    size = int(config["size"])
    if min(width, height, size) <= 0:
        raise ValueError("Invalid template dimensions")
    scale = min(1, size / width, size / height)
    return {"code": code, "width": math.floor(width * scale),
            "height": math.floor(height * scale), "scale": scale,
            "maxFlatId": largest["id"], "maxFlatScale": scale,
            "maxFlatWidth": max_width, "maxFlatHeight": max_height}


def download_material(code, template, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    doc = api_get("image", material_params(code, template))
    data = doc["data"]
    if not isinstance(data, dict) or data.get("code") != code:
        raise AccessError("素材编号与接口结果不一致")
    url = data.get("large", "")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not (parsed.hostname or "").endswith((".aliyuncs.com", ".8ding.com"))):
        raise AccessError("素材接口未提供已支持的原图 CDN 地址；不要猜测或改写缩略图 URL")
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise AccessError("原图格式需要人工检查")
    image_file = out / ("source" + suffix)
    image_file.write_bytes(request_bytes(url))
    write_json(out / "material.json", doc)
    info = {"source_site": "podqhy.com", "gallery_code": code, "title": data.get("name"),
            "source_url": url, "original_width": data.get("largeWidth"),
            "original_height": data.get("largeHeight"), "local_file": str(image_file.resolve()),
            "usage": "workflow_test_pending_user_confirmation"}
    write_json(out / "source-info.json", info)
    return info


def positive_id(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("ID must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    template = sub.add_parser("template")
    template.add_argument("--model-id", required=True, type=positive_id)
    template.add_argument("--color-id", required=True, type=positive_id)
    template.add_argument("--out", required=True)
    material = sub.add_parser("material")
    material.add_argument("--code", required=True)
    material.add_argument("--template", required=True)
    material.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        if args.command == "template":
            result = fetch_template(args.model_id, args.color_id, args.out)
        else:
            result = download_material(args.code, json.loads(Path(args.template).read_text()), args.out)
    except (AccessError, ValueError, KeyError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
