#!/usr/bin/env python3
"""Render offline Markdown/HTML from one run's JSON without calling a backend."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from listing_scope import require_single_product


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "profile": "01_product_profile.json", "kw_raw": "02_kw_raw.json",
    "kw_removed": "03_kw_removed.json", "kw_filtered": "03_kw_filtered.json",
    "kw_tagged": "04_kw_tagged.json", "title_keywords": "05_title_keywords.json",
    "qa": "06_qa.json", "listing": "07_listing.json", "validation": "08_validation.json",
    "review": "08_semantic_review.json", "backend": "08_backend_validation.json",
    "kw_pending": "03_kw_pending.json", "keyword_decisions": "03_keyword_decisions.json",
    "coverage": "07_keyword_coverage.json",
}
FIELDS = ("bullets", "description", "search_terms")
LABELS = {
    "title": "Title", "item_highlight": "Item Highlight", "bullets": "Bullet Points",
    "description": "Description", "search_terms": "Search Terms", "claims": "声明引用",
    "applies_to": "适用范围", "buyer_question": "买家问题", "selling_point": "核心卖点",
    "visual_evidence": "画面证据", "scene_intent": "使用场景与意图", "overlay_text": "图中文字",
    "native_text": "原生模块文案",
    "fact_ids": "事实引用", "measurement_ids": "参数引用", "source_image_ids": "素材引用",
    "body_refs": "正文对应位置", "mobile_check": "移动端检查", "production_notes": "制作说明",
    "role": "图片角色", "direction": "画面方向", "correction": "修正说明",
    "sku": "SKU",
}


class ReportError(ValueError):
    """Invalid source data; never silently render a success report."""


def reject_constant(value):
    raise ValueError(f"JSON 不支持数值 {value}")


def read_json(path: Path, required: bool = False):
    if not path.exists():
        if required:
            raise ReportError(f"缺少必需文件：{path.name}")
        return None, None
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8-sig"), parse_constant=reject_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReportError(f"无法读取有效 JSON：{path.name}（{exc}）") from exc
    if not isinstance(value, (dict, list)):
        raise ReportError(f"{path.name} 的根节点必须是对象或数组")
    return value, raw


def script_json(value) -> str:
    """JSON script elements are raw text: escape markup delimiters, not HTML entities."""
    return (json.dumps(value, ensure_ascii=False, allow_nan=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def as_text(value) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return str(value)


def md_escape(value) -> str:
    text = html.escape(as_text(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]#!|~])", r"\\\1", text)


class Document:
    """Build Markdown and escaped HTML together; user copy is always plain text."""

    def __init__(self, report_view=False):
        self.md = []
        self.html = []
        self.report_view = report_view

    def heading(self, text, level=2, anchor=None):
        self.md.append("#" * level + " " + md_escape(text))
        cls = "listing-title" if level == 2 else "listing-h3"
        identifier = f' id="{html.escape(anchor, quote=True)}"' if anchor else ""
        self.html.append(f'<h{level} class="{cls}"{identifier}>{html.escape(as_text(text))}</h{level}>')

    def value(self, value):
        if self.report_view and isinstance(value, (dict, list)):
            value = readable(value)
        if isinstance(value, (dict, list)):
            serialized = as_text(value)
            fence = "~" * max(3, max((len(m.group()) + 1 for m in re.finditer(r"~+", serialized)), default=3))
            self.md.append(f"{fence}json\n{serialized}\n{fence}")
            self.html.append(f'<pre class="source-json">{html.escape(serialized)}</pre>')
        else:
            self.md.append(md_escape(value))
            if self.report_view:
                self.html.extend("<p>" + html.escape(paragraph).replace("\n", "<br>") + "</p>" for paragraph in as_text(value).split("\n\n") if paragraph.strip())
            else:
                self.html.append("<p>" + html.escape(as_text(value)).replace("\n", "<br>") + "</p>")

    def field(self, key, value, level=3):
        if key == "claims":
            if self.report_view:
                return
            details = Document()
            details.heading(LABELS[key], level)
            details.value(value)
            _, content = details.result()
            self.html.append('<details class="source-details"><summary>声明引用与事实依据</summary>' + content + '</details>')
            return
        self.heading(LABELS.get(key, key), level)
        if key == "bullets" and isinstance(value, list):
            self.md.append("\n".join("- " + md_escape(item).replace("\n", "\n  ") for item in value) or "未提供")
            items = []
            for item in value:
                text = html.escape(as_text(item))
                if self.report_view:
                    text = re.sub(r"【([^】]+)】", r'<strong class="k2">【\1】</strong>', text)
                items.append("<li>" + text.replace("\n", "<br>") + "</li>")
            self.html.append("<ul>" + "".join(items) + "</ul>")
        elif key == "search_terms" and self.report_view and isinstance(value, str):
            self.md.append(md_escape(value))
            self.html.append('<div class="search-terms-list">' + ''.join('<span class="search-term">' + html.escape(word) + '</span>' for word in value.split()) + '</div>')
        else:
            self.value(value)

    def note(self, label, value):
        if value is None or value == "" or value == []:
            return
        text = readable(value)
        self.md.append(md_escape(label + "：" + text))
        self.html.append("<p><strong>" + html.escape(label) + "：</strong>" + html.escape(text).replace("\n", "<br>") + "</p>")

    def details(self, label, value):
        if self.report_view:
            return
        self.html.append('<details class="source-details"><summary>' + html.escape(label) + '</summary><pre class="source-json">' + html.escape(as_text(value)) + '</pre></details>')

    def copy(self, value, level=3):
        self.title_pair(value, level)
        for field in FIELDS:
            self.field(field, value.get(field), level)
        if "claims" in value:
            self.field("claims", value["claims"], level)

    def title_pair(self, value, level=3):
        if not self.report_view:
            for key in ("title", "item_highlight"):
                self.field(key, value.get(key), level)
            return
        limits = json.loads((ROOT / "knowledge/quality_policy.json").read_text(encoding="utf-8"))["default_limits"]
        for key in ("title", "item_highlight"):
            self.md.extend(["#" * level + " " + LABELS[key], md_escape(value.get(key))])
        title, highlight = (as_text(value.get(key)) for key in ("title", "item_highlight"))
        self.html.append('<div class="listing-title-pair" lang="ja">'
                         '<h3 class="product-title">' + html.escape(title) + '</h3>'
                         '<p class="product-highlight">' + html.escape(highlight) + '</p></div>')
        counts = "标题 %d/%d 字符 · 小标题 %d/%d 字符" % (
            len(title), limits["title_max_chars"], len(highlight), limits["item_highlight_max_chars"])
        self.html.append('<p class="copy-lengths">' + counts + '</p>')

    def result(self):
        return "\n\n".join(self.md) + "\n", "\n".join(self.html)


def ensure_object(value, label):
    if not isinstance(value, dict):
        raise ReportError(f"{label} 必须是对象")
    return value


def ensure_array(value, label):
    if not isinstance(value, list):
        raise ReportError(f"{label} 必须是数组")
    return value


def canonical_fingerprint(value):
    if value is None:
        return None
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def effective_status(validation, fingerprints, gaps, evidence_fingerprints=None):
    issues = []
    if validation is None:
        return {"status": "incomplete", "label": "尚未完成验收", "issues": ["缺少 08_validation.json；生成报告不代表验收通过。"] + gaps}
    ensure_object(validation, "08_validation.json")
    recorded = validation.get("fingerprints")
    if not isinstance(recorded, dict) or any(recorded.get(key) != value for key, value in fingerprints.items()) or any(key not in recorded for key in fingerprints):
        issues.append("验收未绑定当前产品画像、Listing 或 QA 文件，或文件已变更；必须重新验收。")
    recorded_evidence = validation.get("evidence_fingerprints")
    if not evidence_fingerprints or any(value is None for value in evidence_fingerprints.values()):
        issues.append("缺少原始语义复审或后端验收记录；不能仅凭汇总声明通过。")
    if not isinstance(recorded_evidence, dict) or recorded_evidence != evidence_fingerprints:
        issues.append("原始语义复审或后端验收记录与汇总不匹配；必须重新验收。")
    declared = validation.get("status")
    stages = [validation.get(key) for key in ("local", "semantic", "backend", "keywords")]
    stage_statuses = [stage.get("status") if isinstance(stage, dict) else stage for stage in stages]
    errors = validation.get("issues", [])
    ensure_array(errors, "08_validation.json.issues")
    has_errors = any(isinstance(issue, dict) and issue.get("severity") == "error" for issue in errors)
    has_incomplete = any(isinstance(issue, dict) and issue.get("severity") == "incomplete" for issue in errors)
    if declared == "failed" or any(value in ("failed", "fail") for value in stage_statuses) or has_errors:
        status = "failed"
    elif declared == "passed" and all(value == "passed" for value in stage_statuses) and not issues and not gaps and not has_incomplete:
        status = "passed"
    else:
        status = "incomplete"
        if declared != "passed" or any(value != "passed" for value in stage_statuses):
            issues.append("本地、筛词、语义复审、后端检查尚未全部通过；不能交付为已通过。")
    issues.extend(gaps)
    for issue in errors:
        if isinstance(issue, dict):
            issues.append(f"{as_text(issue.get('path', ''))}：{as_text(issue.get('message', ''))} [{as_text(issue.get('code', ''))}]")
        else:
            issues.append(as_text(issue))
    return {"status": status, "label": {"passed": "验收通过", "failed": "验收失败", "incomplete": "尚未完成验收"}[status], "issues": issues}


def readable(value):
    """Explain structured values without leaking machine JSON into the copy file."""
    if isinstance(value, list):
        return "；".join(readable(item) for item in value) or "无"
    if isinstance(value, dict):
        return "；".join(str(key) + "：" + readable(item) for key, item in value.items()) or "无"
    return as_text(value)


def a_plus_plans(listing):
    if "a_plus_plan" in listing and "aplus_plan" in listing and listing["a_plus_plan"] != listing["aplus_plan"]:
        raise ReportError("a_plus_plan 与旧字段 aplus_plan 内容冲突")
    return listing.get("a_plus_plan", listing.get("aplus_plan"))


def source_catalog(profile):
    catalog = {"fact_ids": {}, "measurement_ids": {}, "source_image_ids": {}}
    for node in [profile]:
        for collection, reference, identity in (("facts", "fact_ids", "fact_id"), ("measurements", "measurement_ids", "measurement_id"), ("images", "source_image_ids", "image_id")):
            for record in node.get(collection, []):
                if isinstance(record, dict) and record.get(identity):
                    catalog[reference][record[identity]] = record
    return catalog


def explain_references(plan, catalog, key):
    output = []
    for reference in plan.get(key, []):
        record = catalog[key].get(reference)
        if record is None:
            output.append("引用未找到：" + str(reference))
            continue
        if key == "source_image_ids":
            output.append(readable(record.get("source", record.get("path", "素材路径未提供"))))
            continue
        source = record.get("source_ref")
        if key == "measurement_ids":
            value = record.get("display_text")
            if not value:
                value = " ".join(as_text(record.get(field, "")) for field in ("display_value", "display_unit")).strip()
            if not value:
                value = " ".join(as_text(record.get(field, "")) for field in ("value", "unit")).strip()
            label = readable(record.get("label", "规格"))
            output.append(label + "：" + value + ("（来源：" + readable(source) + "）" if source else ""))
        else:
            output.append(readable(record.get("value")) + ("（来源：" + readable(source) + "）" if source else ""))
    return output


def body_locations(value):
    def label(reference):
        reference = str(reference)
        reference = re.sub(r"bullets\[(\d+)\]", lambda match: "五点第" + str(int(match[1]) + 1) + "条", reference)
        return reference.replace("item_highlight", "Item Highlight").replace("description", "长描").replace("search_terms", "搜索词").replace("title", "标题")
    return [label(item) for item in value] if isinstance(value, list) else label(value)


def render_media_details(doc, plan, catalog):
    if doc.report_view:
        direction = plan.get("direction") or plan.get("visual_evidence")
        selling = plan.get("selling_point")
        sentences = [as_text(direction)] if direction else []
        if selling and as_text(selling) not in as_text(direction or ""):
            sentences.append(as_text(selling))
        if sentences:
            doc.value(" ".join(sentences))
        if plan.get("overlay_text"):
            doc.note("上图文案", plan["overlay_text"])
        if plan.get("native_text"):
            doc.note("模块文案", plan["native_text"])
        if plan.get("status") == "pending" and plan.get("production_notes"):
            doc.note("制作前确认", plan["production_notes"])
        if plan.get("asset_type") == "text":
            doc.value("本模块为纯文字，不计入 A+ 图片数量。")
        return
    if plan.get("status") not in (None, "ready", "complete", "completed"):
        doc.note("策划状态", plan.get("status"))
    if plan.get("asset_type") == "text":
        doc.note("模块形式", "纯文字模块，不计入 A+ 图片数量")
    for key in ("direction", "selling_point", "buyer_question", "visual_evidence", "scene_intent", "overlay_text", "native_text", "correction"):
        doc.note(LABELS.get(key, key), plan.get(key))
    doc.note("事实依据", explain_references(plan, catalog, "fact_ids"))
    doc.note("规格参数", explain_references(plan, catalog, "measurement_ids"))
    doc.note("实物素材", explain_references(plan, catalog, "source_image_ids"))
    if plan.get("body_refs"):
        doc.note("正文对应位置", body_locations(plan["body_refs"]))
    for key in ("mobile_check", "production_notes"):
        doc.note(LABELS[key], plan.get(key))
    doc.details("事实引用与制作校验明细", plan)


def media_label(plan, fallback, aplus=False):
    keys = ("module", "image") if aplus or plan.get("role") == "aplus" else ("image", "module")
    return next((plan[key] for key in keys if plan.get(key)), plan.get("plan_id", fallback))


def render_media(doc, profile, listing):
    catalog = source_catalog(profile)
    for plans, heading, anchor in ((listing.get("image_plan"), "附图策划", "image-plans"),
                                   (a_plus_plans(listing), "A+ 整体策划", "a-plus-plans")):
        doc.heading(heading, anchor=anchor)
        if not isinstance(plans, list):
            doc.value(readable(plans))
            continue
        for number, plan in enumerate(plans, 1):
            if not isinstance(plan, dict):
                doc.value(readable(plan))
                continue
            doc.heading(media_label(plan, f"方案 {number}", aplus=anchor == "a-plus-plans"), 3)
            render_media_details(doc, plan, catalog)


def render_coverage(doc, listing):
    coverage = listing.get("buyer_question_coverage", [])
    doc.heading("买家问题覆盖清单")
    if not isinstance(coverage, list):
        doc.value(readable(coverage))
        return
    if not coverage:
        doc.value("未提供买家问题覆盖记录。")
        return
    lines, texts = [], []
    for number, item in enumerate(coverage, 1):
        if isinstance(item, dict):
            question = readable(item.get("question", "未提供问题"))
            location = readable(item.get("location", item.get("answer", item.get("status", "未提供覆盖位置"))))
            text = question + " → " + location
        else:
            text = readable(item)
        lines.append(f"{number}. " + md_escape(text))
        texts.append(text)
    doc.md.append("\n".join(lines))
    if doc.report_view:
        doc.html.extend(f'<p>{number}. {html.escape(text)}</p>' for number, text in enumerate(texts, 1))
    else:
        doc.html.append('<ol class="coverage-list">' + "".join("<li>" + html.escape(text) + "</li>" for text in texts) + "</ol>")


def render_attributes(doc, listing):
    """Backend structured-attribute suggestions (filters and Alexa for Shopping read them)."""
    suggestions = listing.get("attribute_suggestions")
    if not isinstance(suggestions, list) or not suggestions:
        return
    doc.heading("后台属性建议")
    texts = []
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        scope = item.get("applies_to")
        scope_text = "" if scope in (None, ["all"]) else "（适用：" + "、".join(map(as_text, scope)) + "）"
        note = item.get("note")
        texts.append(f"{as_text(item.get('attribute'))}：{as_text(item.get('value'))}{scope_text}" + (f" —— {as_text(note)}" if note else ""))
    doc.md.append("\n".join("- " + md_escape(text) for text in texts))
    if doc.report_view:
        doc.html.extend(f"<p>{html.escape(text)}</p>" for text in texts)
    else:
        doc.html.append("<ul>" + "".join("<li>" + html.escape(text) + "</li>" for text in texts) + "</ul>")


def render_listing(profile, listing, report_view=False):
    doc, gaps = Document(report_view=report_view), []
    require_single_product(profile, listing)
    doc.copy(listing, level=2)
    render_attributes(doc, listing)
    render_media(doc, profile, listing)
    render_coverage(doc, listing)
    for key in ("excluded_claims", "claim_controls"):
        if key in listing:
            doc.details("未采用宣称与边界", listing[key])
    return doc, gaps


def current_local_issues(profile, listing, qa):
    """Recheck current rules, including when imported outside scripts/ by tests."""
    spec = importlib.util.spec_from_file_location("listing_report_quality", ROOT / "scripts/listing_quality.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_local(profile, listing, qa)


def current_keyword_stage(run_dir):
    spec = importlib.util.spec_from_file_location("listing_report_keywords", ROOT / "scripts/keyword_quality.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_run(run_dir)


def report_kw_raw(raw):
    """Spreadsheet imports keep every cell in 02_kw_raw.json; the page only needs metrics.

    Embedding per-row raw_cells made reports many megabytes without adding visible content.
    """
    if not isinstance(raw, dict) or raw.get("source_kind") != "user_spreadsheets":
        return raw
    slim = {key: raw.get(key) for key in ("source_kind", "import_schema_version", "site", "import_counts", "import_policy") if key in raw}
    slim["source_files"] = [{k: v for k, v in f.items() if k != "sheets"} | {"sheets": [{k: v for k, v in sheet.items() if k in ("name", "status", "imported_rows")} for sheet in f.get("sheets", [])]}
                            for f in raw.get("source_files", []) if isinstance(f, dict)]
    issues = raw.get("import_issues") if isinstance(raw.get("import_issues"), list) else []
    slim["import_issue_counts"] = {}
    for issue in issues:
        code = issue.get("code") if isinstance(issue, dict) else "unknown"
        slim["import_issue_counts"][code] = slim["import_issue_counts"].get(code, 0) + 1
    slim["raw"] = {"keyword_data": (raw.get("raw") or {}).get("keyword_data", [])}
    return slim


def build_report(run_dir: Path, generated_at=None):
    data, snapshots = {}, {}
    for key, filename in FILES.items():
        data[key], snapshots[filename] = read_json(run_dir / filename, required=key in ("profile", "listing"))
    profile, listing = ensure_object(data["profile"], FILES["profile"]), ensure_object(data["listing"], FILES["listing"])
    for key in ("kw_removed", "kw_filtered", "kw_tagged", "kw_pending"):
        if data[key] is not None:
            ensure_array(data[key], FILES[key])
    for key in ("kw_raw", "title_keywords", "review", "backend", "coverage"):
        if data[key] is not None:
            ensure_object(data[key], FILES[key])
    doc, gaps = render_listing(profile, listing)
    fingerprints = {f"{key}_sha256": hashlib.sha256(snapshots[FILES[key]]).hexdigest() if snapshots[FILES[key]] is not None else None for key in ("profile", "listing", "qa")}
    evidence_fingerprints = {f"{key}_sha256": canonical_fingerprint(data[key]) for key in ("review", "backend")}
    keywords = current_keyword_stage(run_dir)
    evidence_fingerprints["keywords_sha256"] = canonical_fingerprint(keywords)
    validation = effective_status(data["validation"], fingerprints, gaps, evidence_fingerprints)
    validation["current_keywords"] = keywords
    if keywords["status"] != "passed":
        if validation["status"] != "failed":
            validation["status"] = keywords["status"]
        validation["label"] = {"failed": "验收失败", "incomplete": "尚未完成验收"}[validation["status"]]
        validation["issues"].append("筛词审查尚未通过；历史词表或报告生成不代表审查完成。")
    local_issues = current_local_issues(profile, listing, data["qa"])
    current_status = "failed" if any(issue.get("severity") == "error" for issue in local_issues) else "incomplete" if any(issue.get("severity") == "incomplete" for issue in local_issues) else "passed"
    validation["current_local"] = {"status": current_status, "issues": local_issues}
    if current_status != "passed":
        legacy = any(issue.get("code") == "legacy_schema" for issue in local_issues)
        if validation["status"] != "failed":
            validation["status"] = "incomplete" if legacy else current_status
        validation["label"] = {"passed": "验收通过", "failed": "验收失败", "incomplete": "尚未完成验收"}[validation["status"]]
        validation["issues"].append("按当前规则重新检查尚未通过；详见当前本地校验，历史汇总不能代替本次检查。")
    stamp = generated_at or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    listing_md, _ = doc.result()
    report_doc, _ = render_listing(profile, listing, report_view=True)
    _, listing_html = report_doc.result()
    metadata = {"generated_at": stamp, "missing_files": [name for name, raw in snapshots.items() if raw is None], "fingerprints": fingerprints, "evidence_fingerprints": evidence_fingerprints}
    replacements = {"__DATA_" + key.upper() + "__": script_json(data[key]) for key in ("profile", "kw_removed", "kw_filtered", "kw_tagged", "title_keywords", "qa", "kw_pending", "coverage")}
    replacements["__DATA_KW_RAW__"] = script_json(report_kw_raw(data["kw_raw"]))
    replacements.update({"__DATA_META__": script_json(metadata), "__DATA_LISTING__": script_json(listing),
                         "__DATA_VALIDATION__": script_json(validation),
                         "__DATA_EVIDENCE__": script_json({key: data[key] for key in ("validation", "review", "backend", "keyword_decisions")}),
                         "__DATA_KEYWORD_AUDIT__": script_json(keywords),
                         "__LISTING_HTML__": listing_html, "__VALIDATION_LABEL__": html.escape(validation["label"]),
                         "__VALIDATION_STATUS__": validation["status"]})
    template = (ROOT / "tools/listing_report_template.html").read_text(encoding="utf-8")
    tokens = set(re.findall(r"__[A-Z][A-Z0-9_]*__", template))
    missing = tokens - replacements.keys()
    if missing:
        raise ReportError("报告模板存在未知占位符：" + ", ".join(sorted(missing)))
    rendered = re.sub(r"__[A-Z][A-Z0-9_]*__", lambda match: replacements[match.group()], template)
    # Do not associate outputs with data that changed while the report was built.
    for filename, raw in snapshots.items():
        path = run_dir / filename
        if (path.read_bytes() if path.exists() else None) != raw:
            raise ReportError(f"生成期间文件已变化：{filename}；请重新生成")
    return listing_md, rendered, validation["status"]


def write_outputs(run_dir, markdown, report):
    temporary = []
    try:
        for name, content in (("07_listing.md", markdown), ("report.html", report)):
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=run_dir, prefix=".listing-report-", delete=False) as handle:
                handle.write(content)
                temporary.append((Path(handle.name), run_dir / name))
        for source, destination in temporary:
            os.replace(source, destination)
    finally:
        for source, _ in temporary:
            source.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        markdown, report, status = build_report(args.run_dir)
        write_outputs(args.run_dir, markdown, report)
    except (ReportError, OSError) as exc:
        print(f"报告生成失败：{exc}", file=sys.stderr)
        return 1
    except (TypeError, AttributeError, KeyError) as exc:
        print(f"报告生成失败：嵌套数据结构不符合要求（{exc}）；请检查对象、数组及编号字段。", file=sys.stderr)
        return 1
    print(json.dumps({"rendered": True, "validation_status": status, "files": ["07_listing.md", "07_listing.json", "report.html"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
