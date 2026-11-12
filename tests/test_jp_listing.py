"""Japanese Listing import, evidence isolation, validation and report regressions."""

import contextlib
import copy
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1] / "skills/amazon-jp-listing"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


imports = module("jp_imports_under_test", "import_keywords.py")
keywords = module("jp_keywords_under_test", "keyword_quality.py")
quality = module("jp_quality_under_test", "listing_quality.py")
renderer = module("jp_renderer_under_test", "render_listing.py")
environment = module("jp_environment_under_test", "listing_environment.py")
backend = module("jp_backend_under_test", "backend_cli.py")


def write(run, name, value):
    path = run / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def jp_fixture():
    """Synthetic inputs validate software behavior; they are not product evidence."""
    profile = {
        "schema_version": "2.1", "site": "JP",
        "listing_language": "Japanese", "listing_language_code": "ja", "brand": "テスト",
        "category": "文具収納", "product_identity": {
            "original_name": "测试笔筒", "canonical_name": "ペン立て", "protected_terms": ["ペン立て"]},
        "facts": [{"fact_id": "material", "field": "material", "value": "ステンレス",
                   "status": "confirmed", "source_ref": "synthetic-test:material"}],
        "measurements": [{"measurement_id": "height", "kind": "length", "subject": "product",
                          "label": "height", "value": "15", "unit": "cm", "source_ref": "synthetic-test:drawing"}],
        "images": [{"image_id": "photo", "source": "synthetic-test:photo", "applies_to": ["all"]}],
        "forbidden_terms": [], "intent_map": [{"relation": "product_type", "expression": "文具",
                                               "fact_ids": [], "applies_to": ["all"]}]}
    profile = quality.normalize_profile(profile)
    claims = [{"field": field, "fact_ids": ["material"],
               "measurement_ids": ["height"] if field == "description" else []} for field in quality.FRONT_FIELDS]
    listing = {
        "schema_version": "2.1", "site": "JP", "listing_language": "Japanese",
        "listing_language_code": "ja", "brand_name": "テスト", "title": "テスト ペン立て ステンレス製",
        "item_highlight": "文具をまとめて収納できるペン立てです。",
        "bullets": ["【素材】ステンレス製の本体です。", "【収納】文具をまとめて収納できます。",
                    "【設置】設置場所の寸法をご確認ください。", "【形状】写真で本体の形状をご確認ください。",
                    "【確認】ご購入前に仕様をご確認ください。"],
        "description": "ステンレス製のペン立てです。高さは15 cmです。", "search_terms": "文具収納",
        "claims": claims, "excluded_claims": [],
        "attribute_suggestions": [{"attribute": "material", "value": "ステンレス", "fact_ids": ["material"]}],
        "buyer_question_coverage": [{"question": question, "status": status, "location": location,
                                     "source": "local_editorial"} for question, status, location in [
            ("素材は何ですか？", "covered", "标题与描述"), ("高さは？", "covered", "描述"),
            ("何に使いますか？", "covered", "Highlight"), ("お手入れ方法は？", "not_claimed", "缺资料"),
            ("付属品は？", "not_claimed", "缺资料"), ("屋外で使えますか？", "not_claimed", "缺资料")]],
        "image_plan": [], "a_plus_plan": []}
    def plan(pid, role, number):
        return {"plan_id": pid, "role": role, "applies_to": ["all"],
                "image": "图1（主图）" if role == "main" else "图%d（产品展示）" % number,
                "module": "模块%d：产品信息" % number,
                "asset_type": "image", "direction": "使用测试素材展示产品，不代表真实商品已审核。",
                "buyer_question": "材质与形状是什么？", "selling_point": "测试材质", "visual_evidence": "测试素材",
                "scene_intent": "文具收纳", "overlay_text": "" if role == "main" else "ステンレス製",
                "fact_ids": ["material"], "measurement_ids": [], "source_image_ids": ["photo"],
                "body_refs": ["description"], "mobile_check": "检查小屏文字", "production_notes": "仅为软件测试数据"}
    listing["image_plan"] = [plan("main", "main", 1)] + [plan("secondary-%d" % n, "secondary", n) for n in range(2, 8)]
    listing["a_plus_plan"] = [plan("aplus-%d" % n, "aplus", n) for n in range(1, 6)]
    return profile, listing


def jp_compatibility_fixture():
    """Synthetic long lists cover omissions, suffixes, scope and payload preservation."""
    profile, listing = jp_fixture()
    groups = [["HD-%dA" % n for n in range(5000, 5060)] + ["HD-5016E4"],
              ["HD-RX%d" % n for n in range(5000, 5060)],
              ["HD-RXT%dE9" % n for n in range(5000, 5060)]]
    for index, models in enumerate(groups):
        fid = "fit-%d" % index
        profile["facts"].append({"fact_id": fid, "field": "compatible_models", "value": models,
                                  "group_label": "测试分组%d" % index, "status": "confirmed",
                                  "source_ref": "synthetic-test:compatibility-%d" % index})
        listing["bullets"][index] = "【交換フィルター・対応機種%d】" % (index + 1) + "、".join(models)
        listing["claims"][index + 2]["fact_ids"].append(fid)
    listing["bullets"][3] = "【ペン立て・仕様】ステンレス製。高さ15 cm。"
    listing["claims"][5]["measurement_ids"] = ["height"]
    return profile, listing, groups


class JapaneseListingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / "run"
        environment.init_run(self.run)
        self.profile, self.listing = jp_fixture()
        write(self.run, "01_product_profile.json", self.profile)
        self.csv = self.root / "keywords-JP.csv"
        self.csv.write_text("日文关键词,易逊月搜索量,易逊流量占比,全部来源\n"
                            "ペン立て,0,0%,Amazon联想\n文具収納,,,官网原词\n"
                            "鉄製 ペン立て,50,,Amazon联想\n犬用 ドア,100,,Amazon联想\n", encoding="utf-8-sig")

    def invoke(self, script, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], capture_output=True, text=True)

    def import_and_classify(self):
        imported = self.invoke("import_keywords.py", "--files", self.csv, "--run-dir", self.run)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        keywords.prepare(self.run)
        groups = {}
        decisions = [("ペン立て", "eligible", "identity"), ("文具収納", "eligible", "synonym"),
                     ("鉄製 ペン立て", "deferred", "unknown_fact"), ("犬用 ドア", "excluded", "product_mismatch")]
        for index, (word, decision, code) in enumerate(decisions):
            groups["intent-%d" % index] = {
                "keywords": [word], "decision": decision, "reason_code": code, "reason": "按合成测试的产品身份与材质核对完整意图。",
                "query_intent": word, "evidence": "synthetic-test:identity-and-material", "role": "identity",
                "label": "relevant", "fact_ids": [], "measurement_ids": [], "identity_basis": decision == "eligible",
                "applies_to": ["all"], "promotion_condition": "取得铁材质证明后重新评估" if decision == "deferred" else ""}
        path = write(self.run, "03_keyword_groups.json", {"reviewer": "synthetic-test-reviewer", "groups": groups})
        self.assertEqual(keywords.apply_groups(self.run, path)["unreviewed"], 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(keywords.main(["export", "--run-dir", str(self.run)]), 0)
        write(self.run, "05_title_keywords.json", {
            "title_keywords": {"high": ["ペン立て"], "relevant": ["文具収納"]}, "protected_terms": ["ペン立て"],
            "field_strategy": [{"target": "single", "title": "保留ペン立て，词表搜索量为0；其他词无指标。",
                                "item_highlight": "数量缺资料，未写几件装；仅写已确认的文具用途。",
                                "bullet_openings": ["素材／ペン立て", "文具収納", "設置／ペン立て", "形状／ペン立て", "確認／ペン立て"],
                                "specifications": "软件测试夹具，材质在第一点，尺寸在描述；本例未通过语义布局复审。", "exceptions": []}],
            "placement_plan": [{"keyword": "ペン立て", "target_field": "title", "applies_to": ["all"], "reason": "保留商品身份"},
                               {"keyword": "文具収納", "target_field": "search_terms", "applies_to": ["all"], "reason": "相关别称"}]})
        write(self.run, "07_listing.json", self.listing)

    def test_initialization_is_jp_offline_and_refuses_overwrite(self):
        qa = json.loads((self.run / "06_qa.json").read_text())
        self.assertEqual((qa["site"], qa["status"], qa["reason_code"]), ("JP", "skipped", "rufus_us_only"))
        with self.assertRaises(FileExistsError):
            environment.init_run(self.run)

    def test_grouped_inputs_are_rejected_before_backend_or_report_generation(self):
        for extra in ({"listing_mode": "family", "variants": []}, {"parent": {}},
                      {"shared_content": {}}, {"image_sets": {}},
                      {"variants": []}, {"title_template": "ペン立て"}):
            with self.subTest(extra=extra):
                listing = dict(self.listing, **extra)
                self.assertIn("single_product_only", {i["code"] for i in quality.check_local(self.profile, listing)})
                with self.assertRaises(ValueError):
                    quality.build_payloads(self.profile, listing)
                with self.assertRaises(ValueError):
                    renderer.render_listing(self.profile, listing)
                with patch.object(quality.backend_cli, "run_cli", side_effect=AssertionError("no request for grouped input")):
                    self.assertEqual(quality.run_backend(self.profile, listing)["status"], "failed")
        with self.assertRaises(ValueError):
            quality.normalize_profile(dict(self.profile, variants=[]))

    def test_single_product_media_counts_are_checked_without_grouping_fields(self):
        self.assertNotIn("image_sets", self.listing)
        self.assertFalse([i for i in quality.check_local(self.profile, self.listing) if i["severity"] == "error"])
        self.listing["image_plan"].pop()
        self.assertIn("image_count", {i["code"] for i in quality.check_local(self.profile, self.listing)})
        self.listing["a_plus_plan"][0]["asset_type"] = "text"
        self.assertIn("aplus_image_count", {i["code"] for i in quality.check_local(self.profile, self.listing)})

    def test_direct_backend_rejects_grouped_input_before_cli_setup(self):
        config = write(self.run, "synthetic-config.json", {"backend_url": "https://example.invalid", "backend_token": "synthetic-test-token"})
        grouped = write(self.run, "grouped.json", {"variants": []})
        with patch.object(backend, "select_cli", side_effect=AssertionError("must reject before downloading CLI")):
            result = backend.run_cli("validate", site="JP", listing_file=grouped, config=config)
        self.assertEqual(result["error_code"], "single_product_only")
        self.assertIsNone(result["exit_code"])

    def test_single_backend_payload_and_cache_invalidate_when_copy_changes(self):
        payloads = quality.build_payloads(self.profile, self.listing)
        self.assertEqual([p["target"] for p in payloads], ["single"])
        response = {"exit_code": 0, "response": {"ok": True, "errors": []}}
        with patch.object(quality.backend_cli, "run_cli", return_value=response) as call:
            first = quality.run_backend(self.profile, self.listing)
            self.assertEqual(call.call_count, 1)
        with patch.object(quality.backend_cli, "run_cli", side_effect=AssertionError("reuse unchanged copy")):
            reused = quality.run_backend(self.profile, self.listing, previous=first)
            self.assertTrue(reused["records"][0]["reused"])
        self.listing["description"] += "仕様をご確認ください。"
        with patch.object(quality.backend_cli, "run_cli", return_value=response) as call:
            changed = quality.run_backend(self.profile, self.listing, previous=first)
            self.assertEqual(call.call_count, 1)
            self.assertFalse(changed["records"][0]["reused"])

    def test_current_keyword_headers_zero_missing_and_extra_source_columns(self):
        data = imports.build_import([self.csv], "JP")
        self.assertEqual(data["import_counts"]["raw_records"], 4)
        self.assertEqual(data["keywords"][0]["monthly_searches"], 0)
        self.assertEqual(data["keywords"][0]["traffic_percentage"], 0)
        self.assertIsNone(data["keywords"][1]["monthly_searches"])
        self.assertTrue(any(c["header"] == "全部来源" and c["value"] == "Amazon联想" for c in data["keywords"][0]["raw_cells"]))
        self.assertEqual(data["keywords"][0]["source"]["row"], 2)

    def test_xlsx_import_retains_more_than_150_rows_and_duplicate_positions(self):
        path = self.root / "seed-keywords.xlsx"
        values = [["日文关键词", "易逊月搜索量", "全部来源"]] + [["ペン立て", str(n), "Amazon联想"] for n in range(161)]
        rows = []
        for row_number, row in enumerate(values, 1):
            cells = ['<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>' % (letter, row_number, escape(value))
                     for letter, value in zip("ABC", row)]
            rows.append('<row r="%d">%s</row>' % (row_number, "".join(cells)))
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", '<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="关键词" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr("xl/worksheets/sheet1.xml", '<worksheet><sheetData>%s</sheetData></worksheet>' % "".join(rows))
        data = imports.build_import([path], "JP")
        self.assertEqual(data["import_counts"], {"files": 1, "raw_records": 161, "unique": 1, "duplicates": 160})
        self.assertEqual(len(data["raw"]["keyword_data"][0]["source_positions"]), 161)
        self.assertIsNone(data["raw"]["keyword_data"][0]["monthly_searches"])
        self.assertIn("metric_conflict", {x["code"] for x in data["import_issues"]})

    def test_default_jp_import_site_mismatch_warning_and_snapshot_protection(self):
        wrong = self.root / "keywords-US.csv"
        wrong.write_bytes(self.csv.read_bytes())
        result = self.invoke("import_keywords.py", "--files", wrong, "--run-dir", self.run)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("filename_site_mismatch", json.loads(result.stdout)["issue_counts"])
        self.assertNotEqual(self.invoke("import_keywords.py", "--files", self.csv, "--run-dir", self.run).returncode, 0)
        self.assertNotEqual(self.invoke("import_keywords.py", "--files", self.csv, "--site", "US", "--run-dir", self.run).returncode, 0)

    def test_keyword_pools_and_deferred_terms_are_isolated_from_copy(self):
        self.import_and_classify()
        stage = keywords.check_run(self.run)
        self.assertEqual(stage["status"], "passed", stage["issues"])
        self.listing["search_terms"] += " 鉄製 ペン立て"
        write(self.run, "07_listing.json", self.listing)
        self.assertEqual(keywords.check_run(self.run)["status"], "failed")

    def test_changed_facts_invalidate_the_bound_keyword_review(self):
        self.import_and_classify()
        self.profile["facts"][0]["value"] = "鉄"
        write(self.run, "01_product_profile.json", self.profile)
        self.assertNotEqual(keywords.check_run(self.run)["status"], "passed")

    def test_jp_local_checks_and_utf8_budget(self):
        issues = quality.check_local(self.profile, self.listing, json.loads((self.run / "06_qa.json").read_text()))
        self.assertFalse([i for i in issues if i["severity"] == "error"], issues)
        self.listing["search_terms"] = "語" * 167
        self.assertIn("search_bytes", {i["code"] for i in quality.check_local(self.profile, self.listing)})
        self.profile["site"] = "US"
        with self.assertRaises(ValueError):
            quality.normalize_profile(self.profile)

    def test_new_title_and_highlight_limits_count_spaces_and_fullwidth_once(self):
        for field, limit in (("title", 75), ("item_highlight", 125)):
            with self.subTest(field=field):
                listing = copy.deepcopy(self.listing)
                prefix = "ペン立て A Ｂ "
                listing[field] = prefix + "あ" * (limit - len(prefix))
                self.assertFalse(any(i["code"] == "field_length" for i in quality.check_local(self.profile, listing)))
                listing[field] += " "
                issues = quality.check_local(self.profile, listing)
                self.assertTrue(any(i["code"] == "field_length" and i["path"].endswith("." + field) for i in issues), issues)

    def test_japanese_bullet_labels_preserve_latin_keyword_spelling(self):
        self.listing["bullets"][0] = "【Ag+ フィルター】表記の確認用ソフトウェアテストです。"
        self.assertNotIn("bullet_format", {i["code"] for i in quality.check_local(self.profile, self.listing)})

    def test_long_compatibility_lists_pass_local_budget_and_preserve_full_payload(self):
        profile, listing, groups = jp_compatibility_fixture()
        self.assertGreater(sum(map(len, listing["bullets"])), 1250)
        self.assertTrue(all(len(b) > 250 for b in listing["bullets"][:3]))
        issues = quality.check_local(profile, listing)
        self.assertFalse([i for i in issues if i["severity"] == "error"], issues)
        self.assertIn("compatibility_list_length", {i["code"] for i in issues})
        payload = quality.build_payloads(profile, listing)[0]["payload"]
        self.assertEqual(payload["bullets"], listing["bullets"])
        doc, _ = renderer.render_listing(profile, listing, report_view=True)
        _, page = doc.result()
        for group in groups:
            for model_id in group:
                self.assertIn(model_id, page)

    def test_missing_model_is_not_satisfied_by_series_or_description_only(self):
        profile, listing, groups = jp_compatibility_fixture()
        missing = groups[1][-1]
        listing["bullets"][1] = listing["bullets"][1].replace(missing, "HD-RXシリーズなど")
        listing["description"] += "対応機種：" + missing
        issues = quality.check_local(profile, listing)
        self.assertTrue(any(i["code"] == "compatibility_model_missing" and missing in i["message"] for i in issues), issues)

    def test_complete_model_matching_preserves_suffixes_and_accepts_width_and_dashes(self):
        pattern = quality.model_pattern(["HD-5016E4"])
        for text in ("HD-5016E4", "ｈｄ－５０１６ｅ４", "HD‐5016E4"):
            self.assertTrue(pattern.search(text), text)
        for text in ("HD-5016", "HD-5016E40", "HD-5016E4-W", "HD-5016E4．W", "AHD-5016E4"):
            self.assertIsNone(pattern.search(text), text)
        profile, listing, _ = jp_compatibility_fixture()
        listing["bullets"][0] = listing["bullets"][0].replace("HD-5016E4", "HD-5016")
        self.assertIn("compatibility_model_missing", {i["code"] for i in quality.check_local(profile, listing)})

    def test_uncited_model_lists_cannot_bypass_editorial_limits(self):
        profile, listing, _ = jp_compatibility_fixture()
        for claim in listing["claims"]:
            claim["fact_ids"] = [fid for fid in claim["fact_ids"] if not fid.startswith("fit-")]
        codes = {i["code"] for i in quality.check_local(profile, listing)}
        self.assertIn("compatibility_unreferenced", codes)
        self.assertIn("bullets_length", codes)

    def test_overlapping_sources_do_not_require_repeating_the_same_model(self):
        profile, listing, groups = jp_compatibility_fixture()
        profile["facts"].append({"fact_id": "second-source", "field": "compatible_models", "value": groups[0][:2],
                                  "source_ref": "synthetic-test:second-source", "status": "confirmed"})
        self.assertFalse([i for i in quality.check_local(profile, listing) if i["severity"] == "error"])

    def test_compatibility_exception_does_not_exempt_long_prose(self):
        profile, listing, _ = jp_compatibility_fixture()
        listing["bullets"][0] += "説明" * 140
        self.assertIn("bullets_length", {i["code"] for i in quality.check_local(profile, listing)})

    def test_unknown_or_unsourced_compatibility_cannot_enable_length_exception(self):
        for change, expected in (({"status": "unknown"}, "compatibility_unconfirmed"),
                                 ({"source_ref": ""}, "fact_source")):
            with self.subTest(change=change):
                profile, listing, _ = jp_compatibility_fixture()
                profile["facts"][1].update(change)
                issues = quality.check_local(profile, listing)
                self.assertIn(expected, {i["code"] for i in issues})
                self.assertIn("bullets_length", {i["code"] for i in issues})

    def test_model_fact_requires_individual_full_identifiers(self):
        profile, listing, _ = jp_compatibility_fixture()
        profile["facts"][1]["value"] = ["HD-500A～HD-500F"]
        self.assertIn("compatible_models_shape", {i["code"] for i in quality.check_local(profile, listing)})


    def test_field_layout_feedback_is_advisory_and_keeps_metrics_unknown(self):
        self.import_and_classify()
        self.listing["title"] = "テスト ペン立て"
        self.listing["item_highlight"] = "文具収納用"
        self.listing["bullets"][0] = "【ペン立て】ステンレス製。高さ15 cm。"
        write(self.run, "07_listing.json", self.listing)
        before = json.loads((self.run / "05_title_keywords.json").read_text())["field_strategy"]
        result = keywords.coverage(self.run, write_placement=True)
        layout = result["targets"]["single"]["field_layout"]
        self.assertEqual((layout["title_chars"], layout["item_highlight_chars"]), (len(self.listing["title"]), 5))
        self.assertEqual(len(layout["bullet_openings"]), 5)
        self.assertIn("ペン立て", layout["bullet_openings"][0]["exact_keyword_hits"])
        metrics = {r["keyword"]: r for r in layout["top_keywords"]}
        self.assertEqual(metrics["ペン立て"]["searches"], 0)
        self.assertIsNone(metrics["文具収納"]["searches"])
        self.assertIsNone(metrics["文具収納"]["title_position"])
        self.assertEqual(json.loads((self.run / "05_title_keywords.json").read_text())["field_strategy"], before)

    def test_strategy_requires_all_five_openings_and_every_supplied_target(self):
        self.import_and_classify()
        decisions = json.loads((self.run / "03_keyword_decisions.json").read_text())
        plan = json.loads((self.run / "05_title_keywords.json").read_text())
        self.assertFalse(keywords.check_usage(self.profile, decisions, plan))
        plan["field_strategy"][0]["bullet_openings"].pop()
        self.assertIn("field_strategy_openings", {i["code"] for i in keywords.check_usage(self.profile, decisions, plan)})
        plan["field_strategy"] = []
        self.assertIn("field_strategy_targets", {i["code"] for i in keywords.check_usage(self.profile, decisions, plan)})

    def test_keyword_changes_reset_field_layout_review_but_keep_factual_review(self):
        self.import_and_classify()
        profile_path, listing_path = self.run / "01_product_profile.json", self.run / "07_listing.json"
        previous = quality.make_review_template(profile_path, listing_path)
        for record in previous["records"]:
            record.update(status="pass", evidence="synthetic-test:review")
        plan = json.loads((self.run / "05_title_keywords.json").read_text())
        plan["field_strategy"][0]["title"] += "补充选词依据。"
        write(self.run, "05_title_keywords.json", plan)
        current = quality.make_review_template(profile_path, listing_path, previous=previous)
        checks = {r["check"]: r for r in current["records"] if r["target"] == "single"}
        self.assertEqual(checks["field_layout"]["status"], "pending")
        self.assertEqual(checks["keyword_usage"]["status"], "pending")
        self.assertEqual(checks["factual_support"]["status"], "pass")
        self.assertTrue(checks["field_layout"]["guidance"])

    def test_actual_cli_coverage_review_and_html_cannot_claim_missing_backend_passed(self):
        self.import_and_classify()
        result = self.invoke("keyword_quality.py", "coverage", "--run-dir", self.run, "--write-placement")
        self.assertEqual(result.returncode, 0, result.stderr)
        common = ["--profile", self.run / "01_product_profile.json", "--listing", self.run / "07_listing.json", "--qa", self.run / "06_qa.json"]
        result = self.invoke("listing_quality.py", "review-template", *common, "--output", self.run / "08_semantic_review.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.invoke("listing_quality.py", "check", *common, "--review", self.run / "08_semantic_review.json", "--output", self.run / "08_validation.json")
        self.assertEqual(result.returncode, 1, result.stderr)
        validation = json.loads((self.run / "08_validation.json").read_text())
        self.assertEqual(validation["local"]["status"], "passed", validation["issues"])
        self.assertEqual(validation["status"], "incomplete")
        result = self.invoke("render_listing.py", "--run-dir", self.run)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["validation_status"], "incomplete")
        markdown = (self.run / "07_listing.md").read_text()
        page = (self.run / "report.html").read_text()
        self.assertIn("ペン立て", markdown)
        self.assertNotIn("犬用 ドア", markdown)
        self.assertIn("尚未完成验收", page)
        self.assertNotRegex(page, r"__DATA_[A-Z_]+__")
        self.assertEqual(page.count('class="chapter"'), 7)
        self.assertIn('class="listing-title-pair"', page)
        self.assertIn('class="product-highlight"', page)


    def test_missing_backend_credentials_prevent_download_and_requests(self):
        with patch.object(backend, "select_cli", side_effect=AssertionError("must not download without credentials")):
            result = backend.run_cli("validate", site="JP", listing_file=write(self.run, "input.json", {}),
                                     config=self.root / "absent.json")
        self.assertEqual(result["error_code"], "config_unreadable")


    def test_shared_configuration_rotation_preserves_aba_fields_and_redacts_status(self):
        from listing_config import configure, config_status
        state = self.root / "shared"
        configure(state, {"aba_mcp": {"access_token": "synthetic-aba-secret"}})
        configure(state, {"backend_token": "synthetic-backend-secret"})
        data = json.loads((state / "config.json").read_text())
        self.assertEqual(data["aba_mcp"]["access_token"], "synthetic-aba-secret")
        status = json.dumps(config_status(state))
        self.assertNotIn("synthetic-aba-secret", status)
        self.assertNotIn("synthetic-backend-secret", status)

    def test_validation_binary_integrity_and_verified_cache_reuse(self):
        fixture = b"synthetic-cli"
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps({"revision": "test-revision", "skill_path": "skills/lc_amazon_listing",
                                        "sha256": {"laochen-cli-v2-linux-amd64": sha256(fixture).hexdigest()}}))
        with patch.object(environment, "MANIFEST", manifest), patch.object(environment, "STATE", self.root), \
             patch.object(environment.platform, "system", return_value="Linux"), \
             patch.object(environment.platform, "machine", return_value="x86_64"):
            with patch.object(environment, "urlopen", return_value=io.BytesIO(b"tampered")):
                with self.assertRaises(environment.SetupError):
                    environment.prepare_backend_cli()
            with patch.object(environment, "urlopen", return_value=io.BytesIO(fixture)):
                path = environment.prepare_backend_cli()
            self.assertEqual(path.read_bytes(), fixture)
            with patch.object(environment, "urlopen", side_effect=AssertionError("verified cache must be reused")):
                self.assertEqual(environment.prepare_backend_cli(), path)


if __name__ == "__main__":
    unittest.main()
