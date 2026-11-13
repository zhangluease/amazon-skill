#!/usr/bin/env python3
"""Deterministic Listing v2 checks; semantic truth still needs an independent review.

Python 3.9+, standard library only. No command rewrites generated marketing text.
Fingerprints bind review files to source bytes; backend fingerprints bind canonical
single-product payloads. All backend calls use the shared credential-safe launcher.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path

from listing_scope import require_single_product
from model_ids import normalize_model, model_pattern, valid_model_list

_BACKEND_SPEC = importlib.util.spec_from_file_location("listing_backend_cli", Path(__file__).with_name("backend_cli.py"))
backend_cli = importlib.util.module_from_spec(_BACKEND_SPEC)
_BACKEND_SPEC.loader.exec_module(backend_cli)

POLICY_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "quality_policy.json"
TEXT_FIELDS = ("title", "item_highlight", "description", "search_terms")
FRONT_FIELDS = ("title", "item_highlight") + tuple("bullets[%d]" % i for i in range(5)) + ("description",)
KEYWORD_REVIEW_CHECKS = ("keyword_usage", "field_layout", "compatibility_models")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
LENGTH_UNITS = {"mm", "cm", "m", "in", "ft"}
UNIT_ALIASES = {"inch": "in", "inches": "in", '"': "in", "centimeter": "cm",
                "centimeters": "cm", "millimeter": "mm", "millimeters": "mm",
                "meter": "m", "meters": "m", "feet": "ft", "foot": "ft", "lbs": "lb",
                "milliliter": "ml", "milliliters": "ml", "liter": "l", "liters": "l", "gram": "g", "grams": "g",
                "fluid ounces": "fl oz", "fluid ounce": "fl oz", "floz": "fl oz"}


def _reject_json_constant(value):
    raise ValueError("Non-finite JSON number is not permitted")


def load_json(path):
    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle, parse_constant=_reject_json_constant)


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent),
                                         prefix="." + path.name + ".", delete=False) as handle:
            temporary = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def canonical_sha256(data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_fingerprints(profile_path, listing_path, qa_path=None):
    return {key + "_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None
            for key, path in (("profile", profile_path), ("listing", listing_path), ("qa", qa_path))}


def _list(value):
    return value if isinstance(value, list) else []


def _dict(value):
    return value if isinstance(value, dict) else {}


def _norm(value):
    return re.sub(r"[\s\-‐‑‒–—]+", " ", str(value).casefold()).strip()


def _contains(text, phrase):
    phrase = _norm(phrase)
    # Japanese/Chinese do not require spaces around an intact product phrase.
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", phrase):
        return bool(phrase) and phrase in _norm(text)
    if phrase and phrase[0].isdigit():
        return re.search(r"(?<![A-Za-z0-9_])" + re.escape(phrase) + r"(?![A-Za-z0-9_])", _norm(text)) is not None
    return bool(phrase) and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", _norm(text)) is not None


INTENT_RELATIONS = ("function", "capability", "activity", "audience", "location", "season", "body_part",
                    "companion", "product_type", "interest")
TITLE_REPEAT_EXEMPT = {
    "a", "an", "the", "and", "or", "nor", "but", "for", "with", "without", "in", "on", "of", "to", "by", "at",
    "from", "into", "onto", "over", "under", "as", "per", "vs", "x", "&",
    "der", "die", "das", "und", "oder", "mit", "für", "fur", "von", "zu", "im", "in", "ein", "eine",
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "pour", "avec", "en", "à",
    "el", "los", "las", "y", "o", "para", "con", "del", "il", "lo", "gli", "e", "per", "di", "da", "con",
}
PROHIBITED_BULLET_SYMBOLS = re.compile("[™®©\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _words(text):
    return [w.casefold() for w in re.findall(r"[^\W_]+(?:['’][^\W_]+)*", str(text))]


def _has_lowercase(text):
    # German ß (and similar letters) has no single-character capital in Python; it is allowed.
    return any(ch != ch.upper() and len(ch.upper()) == 1 for ch in text)


def _decimal_text(value):
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


US_PREFERRED_UNITS = {"length": ("in", "ft"), "weight": ("oz", "lb"), "volume": ("fl oz", "gal")}
# Exact factors to the US base unit of each kind (inch, ounce, US fluid ounce).
TO_US_BASE = {
    "length": {"mm": Decimal("1") / Decimal("25.4"), "cm": Decimal("1") / Decimal("2.54"),
               "m": Decimal("100") / Decimal("2.54"), "in": Decimal("1"), "ft": Decimal("12")},
    "weight": {"mg": Decimal("0.001") / Decimal("28.349523125"), "g": Decimal("1") / Decimal("28.349523125"),
               "kg": Decimal("1000") / Decimal("28.349523125"), "oz": Decimal("1"), "lb": Decimal("16")},
    "volume": {"ml": Decimal("1") / Decimal("29.5735295625"), "cl": Decimal("10") / Decimal("29.5735295625"),
               "l": Decimal("1000") / Decimal("29.5735295625"), "fl oz": Decimal("1"), "gal": Decimal("128")},
}
US_LARGE_UNIT = {"length": ("ft", Decimal("12"), Decimal("120")),   # metric >= 10 ft reads as feet
                 "weight": ("lb", Decimal("16"), Decimal("16")),     # metric >= 1 lb reads as pounds
                 "volume": ("gal", Decimal("128"), Decimal("128"))}  # metric >= 1 gal reads as gallons
METRIC_UNITS = {"mm", "cm", "m", "mg", "g", "kg", "ml", "cl", "l"}


def _measurement_display(record, site):
    kind = record.get("kind")
    value, unit = record.get("value"), record.get("unit")
    if not isinstance(value, str) or not value.strip() or not isinstance(unit, str) or not unit.strip():
        raise ValueError("measurement requires a nonempty original string value and unit")
    unit = UNIT_ALIASES.get(unit.strip().lower(), unit.strip().lower())
    if kind in ("nominal", "size"):
        unit = record["unit"].strip()
        return {"display_value": value.strip(), "display_unit": unit,
                "display_text": value.strip() + " " + unit, "approximate": False}
    if kind not in ("length", "weight", "volume"):
        raise ValueError("unknown measurement kind")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("measurement value is not a decimal") from None
    if not number.is_finite() or number <= 0 or number.adjusted() > 12 or number.adjusted() < -12:
        raise ValueError("measurement must be a finite positive practical value")
    if kind == "weight" and unit not in ("mg", "g", "kg", "oz", "lb"):
        raise ValueError("weight unit is unsupported (oz is mass; fl oz is volume)")
    if kind == "volume" and unit not in ("ml", "l", "cl", "fl oz", "gal", "qt", "pt", "cup", "cups"):
        raise ValueError("volume unit is unsupported (fl oz is volume; oz is mass)")
    if kind == "length" and unit not in LENGTH_UNITS:
        raise ValueError("unsupported length unit")
    preferred = record.get("preferred_unit")
    if preferred is not None and (site != "US" or preferred not in US_PREFERRED_UNITS[kind]):
        raise ValueError("preferred_unit is only for US and must be one of %s for %s" % (US_PREFERRED_UNITS[kind], kind))
    approximate = False
    with localcontext() as context:
        context.prec = 50
        # US: metric values are converted; imperial source units stay as supplied unless a
        # preferred US unit is declared (e.g. 6 ft cables, 16 oz tumblers). Other sites keep source.
        if site == "US" and unit in TO_US_BASE[kind]:
            exact = number
            if unit in METRIC_UNITS or preferred:
                base = number * TO_US_BASE[kind][unit]
                large_unit, factor, threshold = US_LARGE_UNIT[kind]
                small_unit = US_PREFERRED_UNITS[kind][0]
                unit = preferred or (large_unit if base >= threshold else small_unit)
                exact = base / factor if unit == large_unit else base
            number = exact.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if number == 0:
                raise ValueError("value rounds to zero in US units; needs an explicit precision exception")
            approximate = number != exact
    shown = _decimal_text(number)
    return {"display_value": shown, "display_unit": unit,
            "display_text": shown + " " + unit, "approximate": approximate}


def normalize_profile(profile):
    """Copy one product and calculate sourced measurement display values."""
    require_single_product(profile)
    result = copy.deepcopy(profile)
    result.pop("listing_mode", None)
    site = result.get("site")
    if site not in load_json(POLICY_PATH)["sites"]:
        raise ValueError("profile.site must name a supported site")
    seen = set()
    for measurement in _list(result.get("measurements")):
        if not isinstance(measurement, dict):
            raise ValueError("measurement must be an object")
        mid = measurement.get("measurement_id")
        if not isinstance(mid, str) or not mid or mid in seen:
            raise ValueError("measurement_id must be present and unique")
        measurement.update(_measurement_display(measurement, site))
        seen.add(mid)
    return result


def resolved_listings(profile, listing):
    """One complete Listing; never merge content from other products."""
    require_single_product(profile, listing)
    return [("single", copy.deepcopy(listing))]


def build_payloads(profile, listing):
    """Return records {target, filename, payload, payload_sha256}; no backend calls."""
    records = []
    for target, content in resolved_listings(profile, listing):
        if not isinstance(target, str) or not SAFE_ID.fullmatch(target):
            raise ValueError("unsafe payload target")
        payload = {key: content.get(key, profile.get(key)) for key in
                   ("site", "listing_language", "listing_language_code", "title", "item_highlight", "bullets", "description", "search_terms")}
        records.append({"target": target, "filename": target + ".json", "payload": payload,
                        "payload_sha256": canonical_sha256(payload)})
    return records


def _field(content, field):
    if field in TEXT_FIELDS:
        return content.get(field)
    match = re.fullmatch(r"bullets\[([0-4])\]", str(field))
    if match:
        bullets = _list(content.get("bullets"))
        index = int(match[1])
        return bullets[index] if index < len(bullets) else None
    return None


def check_media_plans(listing, add):
    """Validate the product's ordered image and A+ arrays directly."""
    limits = load_json(POLICY_PATH)["media_limits"]
    images = [plan for plan in _list(listing.get("image_plan")) if isinstance(plan, dict)]
    for role, key in (("main", "main_count"), ("secondary", "secondary_count")):
        if sum(plan.get("role") == role for plan in images) != limits[key]:
            add("image_count", "listing.image_plan", "Expected %d %s image plans" % (limits[key], role))
    for number, plan in enumerate(images, 1):
        path = "listing.image_plan[%d]" % (number - 1)
        role = "main" if number == 1 else "secondary"
        label = plan.get("image")
        if plan.get("role") != role or not isinstance(label, str) or not re.fullmatch(r"图%d（[^）]+）" % number, label):
            add("image_number", path, "Number the main image first, then secondary images in order")
        elif number == 1 and label != "图1（主图）":
            add("image_number", path, "The first image is 图1（主图）")
    aplus = [plan for plan in _list(listing.get("a_plus_plan", listing.get("aplus_plan"))) if isinstance(plan, dict)]
    if sum(plan.get("role") == "aplus" and plan.get("asset_type") == "image" for plan in aplus) < limits["a_plus_min_images"]:
        add("aplus_image_count", "listing.a_plus_plan", "At least %d A+ images are required; text modules do not count" % limits["a_plus_min_images"])
    for number, plan in enumerate(aplus, 1):
        label = plan.get("module")
        if not isinstance(label, str) or not re.fullmatch(r"模块%d[：:]\S.*" % number, label):
            add("aplus_number", "listing.a_plus_plan[%d]" % (number - 1), "Number A+ modules consecutively as 模块N：主题")


def check_local(profile, listing, qa=None):
    """Return deterministic issues; not a language detector or claim-truth oracle."""
    try:
        # Public dict API follows the same strict JSON constraints as the CLI.
        json.dumps([profile, listing, qa], allow_nan=False)
        return _check_local(profile, listing, qa)
    except (TypeError, ValueError, KeyError, AttributeError):
        return [{"severity": "error", "code": "malformed_structure", "path": "$",
                 "message": "Invalid JSON value or nested field type; use the v2 data contract"}]


def _check_local(profile, listing, qa=None):
    issues = []

    def add(code, path, message, severity="error"):
        issues.append({"severity": severity, "code": code, "path": path, "message": message})

    def array(owner, key, path):
        value = owner.get(key)
        if not isinstance(value, list):
            add("array_required", path + "." + key, "Expected an array")
            return []
        return value

    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())

    if not isinstance(profile, dict) or not isinstance(listing, dict):
        add("object_required", "$", "Profile and listing must be JSON objects")
        return issues
    policy = load_json(POLICY_PATH)
    limits = policy["default_limits"]
    try:
        require_single_product(profile, listing)
    except ValueError as error:
        add("single_product_only", "$", str(error))
        return issues
    if profile.get("schema_version") != "2.1" or listing.get("schema_version") != "2.1":
        add("legacy_schema", "$", "Legacy content remains readable; regenerate and review under the current delivery contract before acceptance", "incomplete")
    basis = profile.get("compatibility_basis")
    if basis is not None:
        if basis not in ("model", "specification", "none"):
            add("compatibility_basis", "profile.compatibility_basis", "Use model, specification or none according to the supplied product context")
        if not nonempty(profile.get("compatibility_reason")):
            add("compatibility_reason", "profile.compatibility_reason", "Explain how buyers select this consumable from the supplied information", "incomplete")
        if basis == "model" and not any(isinstance(f, dict) and f.get("field") == "compatible_models" for f in _list(profile.get("facts"))):
            add("compatibility_unconfirmed", "profile.facts", "Model-dependent consumables need supplied fit identifiers; record the actual missing input", "incomplete")
    if "a_plus_plan" in listing and "aplus_plan" in listing and listing["a_plus_plan"] != listing["aplus_plan"]:
        add("aplus_alias_conflict", "listing.a_plus_plan", "a_plus_plan and legacy aplus_plan contain conflicting content")
    a_plus_key = "a_plus_plan" if "a_plus_plan" in listing else "aplus_plan"
    if listing.get("schema_version") == "2.1":
        if "brand_name" not in listing or listing.get("brand_name") != profile.get("brand"):
            add("brand_metadata", "listing.brand_name", "brand_name must preserve the supplied brand or null")
        for key in ("buyer_question_coverage", "excluded_claims"):
            array(listing, key, "listing")
        if any(not nonempty(item) for item in _list(listing.get("excluded_claims"))):
            add("excluded_claims", "listing.excluded_claims", "Excluded claims must be nonempty strings")
        for index, item in enumerate(_list(listing.get("buyer_question_coverage"))):
            if not isinstance(item, dict) or any(not nonempty(item.get(key)) for key in ("question", "status", "location")):
                add("buyer_question_coverage", "listing.buyer_question_coverage[%d]" % index, "Coverage requires a question, status, and readable location or reason")
            elif item["status"] not in ("covered", "partially_covered", "not_claimed", "not_applicable"):
                add("buyer_question_coverage", "listing.buyer_question_coverage[%d].status" % index, "Use covered, partially_covered, not_claimed, or not_applicable")
    site = profile.get("site")
    if site not in policy["sites"]:
        add("site", "profile.site", "A supported site is required; no implicit US default")
    if listing.get("site") != site:
        add("site_mismatch", "listing.site", "Listing site must match profile")
    for key in ("listing_language", "listing_language_code"):
        if not nonempty(profile.get(key)) or listing.get(key) != profile.get(key):
            add("language_metadata", "listing." + key, "Language metadata must be present and match profile")
    if site in policy["sites"]:
        expected = policy["sites"][site]["language_code"]
        if profile.get("listing_language_code") != expected and not (site == "CA" and profile.get("listing_language_code") == "fr"):
            add("site_language", "profile.listing_language_code", "Language code does not match the target site")
    identity = _dict(profile.get("product_identity"))
    for key in ("original_name", "canonical_name"):
        if not nonempty(identity.get(key)):
            add("identity_missing", "profile.product_identity." + key, "Product identity is required")
    protected = _list(identity.get("protected_terms"))
    if not protected or any(not nonempty(term) for term in protected):
        add("protected_terms", "profile.product_identity.protected_terms", "At least one nonempty complete product phrase is required")
    forbidden = _list(profile.get("forbidden_terms"))
    owners = [("all", profile)]

    facts, measurements, images = {}, {}, {}
    for owner_id, owner in owners:
        for key, id_key, dest in (("facts", "fact_id", facts), ("measurements", "measurement_id", measurements)):
            for index, record in enumerate(array(owner, key, "profile." + owner_id)):
                path = "profile.%s.%s[%d]" % (owner_id, key, index)
                if not isinstance(record, dict):
                    add("record_object", path, "Record must be an object")
                    continue
                rid = record.get(id_key)
                if not nonempty(rid) or rid in dest:
                    add("global_id", path + "." + id_key, "Record ID must be globally unique and nonempty")
                    continue
                dest[rid] = (owner_id, record)
                if key == "facts":
                    if not nonempty(record.get("field")) or record.get("status") not in ("confirmed", "unknown", "conflict"):
                        add("fact_shape", path, "Fact requires a field and confirmed/unknown/conflict status")
                    if record.get("status") == "confirmed" and (not nonempty(record.get("source_ref")) or record.get("value") is None or record.get("value") == ""):
                        add("fact_source", path, "Confirmed facts require an explicit value and source")
                    if record.get("field") == "compatible_models":
                        if record.get("status") == "confirmed" and not valid_model_list(record.get("value")):
                            add("compatible_models_shape", path, "Confirmed compatibility requires an array of complete model IDs, one ID per string; no ranges or abbreviations")
                        elif record.get("status") != "confirmed":
                            add("compatibility_unconfirmed", path, "Compatibility evidence is unknown or conflicting; do not invent or imply a complete confirmed list", "incomplete")
                else:
                    if record.get("subject") not in ("product", "package", "fit") or not nonempty(record.get("label")) or not nonempty(record.get("source_ref")):
                        add("measurement_metadata", path, "Measurement requires subject, dimension label, and source")
                    try:
                        display = _measurement_display(record, site)
                        if any(record.get(k) != value for k, value in display.items()):
                            add("measurement_display", path, "Display differs from deterministic conversion; run normalize")
                    except (ValueError, InvalidOperation) as error:
                        add("measurement_invalid", path, str(error))
    length_token = re.compile(r'(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)\s*[-‐‑]?\s*(centimeters?|millimeters?|meters?|inches|inch|in|cm|mm|m|feet|foot|ft|")(?![A-Za-z])', re.I)

    def _dual_source_display(text, match, number, unit, allowed_owners):
        before = text[:match.start()].rstrip()
        if not before.endswith("("):
            return False
        before = before[:-1].rstrip()
        for owner_id, measurement in measurements.values():
            try:
                same_source = (Decimal(str(measurement.get("value"))) == number
                               and UNIT_ALIASES.get(str(measurement.get("unit", "")).strip().lower(), str(measurement.get("unit", "")).strip().lower()) == unit)
            except InvalidOperation:
                same_source = False
            shown = measurement.get("display_text")
            if owner_id in allowed_owners and same_source and isinstance(shown, str) and before.casefold().endswith(shown.casefold()):
                return True
        return False

    def scan_measurements(text, allowed_owners, path):
        matching_groups = []
        if not isinstance(text, str):
            return matching_groups
        for match in length_token.finditer(text):
            number, unit = Decimal(match[1]), UNIT_ALIASES.get(match[2].lower(), match[2].lower())
            if unit == "in" and re.match(r"\s*[-‐‑]?\s*\d", text[match.end():]):
                continue  # "2 in 1" / "3-in-1" is a product claim, not a length.
            if site == "US" and unit in ("mm", "cm", "m") and _dual_source_display(text, match, number, unit, allowed_owners):
                continue  # "5.91 in (15 cm)": the source unit shown after its US display.
            matches, own, nominal = [], [], []
            for mid, (owner_id, measurement) in measurements.items():
                shown_unit = measurement.get("display_unit")
                if isinstance(shown_unit, str):
                    shown_unit = UNIT_ALIASES.get(shown_unit.casefold(), shown_unit.casefold())
                try:
                    same_value = Decimal(str(measurement.get("display_value"))) == number
                except InvalidOperation:
                    same_value = False
                if shown_unit == unit and same_value:
                    matches.append(mid)
                    if owner_id in allowed_owners:
                        own.append(mid)
                        if measurement.get("kind") in ("nominal", "size"):
                            nominal.append(mid)
            if site == "US" and unit in ("mm", "cm", "m") and not nominal:
                add("source_unit_leak", path, "Ordinary US lengths must use normalized inches, including compact or reformatted numeric units")
            elif not own:
                add("measurement_unregistered", path, "Numeric length has no matching registered measurement for this product: " + match[0])
            if own:
                matching_groups.append(set(own))
        return matching_groups

    # Package counts are facts, not lengths or arbitrary digits in materials/models.
    quantity_fields = {"quantity", "package_quantity", "unit_count", "pack_count", "number_of_items"}
    # Included accessories are counted separately and never satisfy the purchase quantity.
    accessory_fields = {"accessory_count", "included_quantity", "component_count"}
    quantities, accessory_counts = {}, {}
    for fid, (owner_id, fact) in facts.items():
        if fact.get("field") in quantity_fields and re.fullmatch(r"[1-9]\d*", str(fact.get("value", ""))):
            quantities[fid] = (owner_id, int(fact["value"]))
        if fact.get("field") in accessory_fields and fact.get("status") == "confirmed" and re.fullmatch(r"[1-9]\d*", str(fact.get("value", ""))):
            accessory_counts[fid] = (owner_id, int(fact["value"]))
    # "N pieces of <item>" describes included parts, not the sellable pack size.
    quantity_token = re.compile(r"\b(\d+)\s*[- ]?\s*(?:packs?|count|pieces?|pcs)\b(?!\s+of\b)|\b(?:pack|set)\s+of\s+(\d+)\b|\bquantity\s*:\s*(\d+)\b", re.I)

    def scan_quantities(text, allowed_owners, path):
        matching_groups = []
        if not isinstance(text, str):
            return matching_groups
        for match in quantity_token.finditer(text):
            number = int(next(value for value in match.groups() if value is not None))
            own = {fid for fid, (owner_id, value) in quantities.items() if owner_id in allowed_owners and value == number}
            if not own and re.search(r"pieces?|pcs", match[0], re.I) and any(
                    owner_id in allowed_owners and value == number for owner_id, value in accessory_counts.values()):
                continue  # A confirmed included-accessory count, e.g. "4 pcs felt pads".
            if not own:
                add("quantity_unregistered", path, "Package count must match a registered quantity fact for this product: " + match[0])
            if own:
                matching_groups.append(own)
        return matching_groups

    def scope(value, path):
        if value != ["all"]:
            add("scope", path, "applies_to must be ['all'] for this product")
            return set()
        return {"single"}

    for index, record in enumerate(array(profile, "images", "profile")):
        path = "profile.images[%d]" % index
        if not isinstance(record, dict):
            add("image_object", path, "Image must be an object")
            continue
        image_id = record.get("image_id")
        if not nonempty(image_id) or image_id in images:
            add("image_id", path, "Image ID must be globally unique and nonempty")
            continue
        image_scope = scope(record.get("applies_to"), path + ".applies_to")
        if not nonempty(record.get("source")):
            add("image_source", path, "Image needs its actual source")
        images[image_id] = (image_scope, record)
    site_policy = policy["sites"].get(site, {}) if isinstance(policy["sites"].get(site), dict) else {}
    search_bytes_limit = site_policy.get("search_terms_max_bytes", limits["search_terms_max_bytes"])
    title_policy = policy.get("title_compliance", {})
    brand_names = [b for b in (profile.get("brand"), listing.get("brand_name")) if nonempty(b)]
    spaced_language = profile.get("listing_language_code") not in ("ja", "zh")

    def check_title_compliance(value, path):
        # Jan 2025 Amazon title rules: banned characters (unless part of the brand) and
        # no word more than twice (articles, prepositions and conjunctions excepted).
        unbranded = value
        for brand in brand_names:
            unbranded = unbranded.replace(brand, " ")
        banned = sorted({ch for ch in unbranded if ch in title_policy.get("banned_characters", "")})
        if banned:
            add("title_banned_character", path, "Title contains characters Amazon disallows outside the brand name: " + " ".join(banned))
        limit = title_policy.get("max_word_repeats")
        if limit and spaced_language:
            counts = {}
            for word in _words(value):
                if word not in TITLE_REPEAT_EXEMPT and not word.isdigit():
                    counts[word] = counts.get(word, 0) + 1
            repeated = sorted(word for word, count in counts.items() if count > limit)
            if repeated:
                add("title_word_repeat", path, "Words used more than %d times in the title: %s" % (limit, ", ".join(repeated)), "warning")

    def check_search_terms(value, title, path):
        if not spaced_language or not value.strip():
            return
        words = _words(value)
        title_words = set(_words(title)) if isinstance(title, str) else set()
        repeated_title = sorted({w for w in words if w in title_words})
        if repeated_title:
            wasted = sum(len(w.encode("utf-8")) + 1 for w in words if w in title_words)
            add("search_terms_title_repeat", path, "Backend words already indexed from the title (%d bytes reusable): %s" % (wasted, ", ".join(repeated_title)), "warning")
        duplicates = sorted({w for w in words if words.count(w) > 1})
        if duplicates:
            add("search_terms_duplicate", path, "Backend words repeated within search terms: " + ", ".join(duplicates), "warning")
        if value != value.lower() or re.search(r"[,;|/!?.]", value):
            add("search_terms_format", path, "Use lowercase words separated by spaces; punctuation is unnecessary", "warning")

    def check_text(target, content):
        path = "listing." + target
        fields = TEXT_FIELDS
        for field in fields:
            value = content.get(field)
            # Empty search terms are legitimate after deduplication.
            if not isinstance(value, str) or (field != "search_terms" and not value.strip()):
                add("text_field", path + "." + field, "Expected a nonempty string (search_terms may be empty)")
                continue
            limit = limits.get(field + "_max_chars")
            if limit is not None and len(value) > limit:
                add("field_length", path + "." + field, "Length %d exceeds %d characters" % (len(value), limit))
            if field == "title":
                check_title_compliance(value, path + ".title")
            if field == "search_terms":
                if len(value.encode("utf-8")) > search_bytes_limit:
                    add("search_bytes", path + ".search_terms", "UTF-8 search terms use %d bytes; the %s budget is %d" % (len(value.encode("utf-8")), site, search_bytes_limit))
                check_search_terms(value, content.get("title"), path + ".search_terms")
        for term in protected:
            if isinstance(term, str) and term and not _contains(content.get("title", ""), term):
                add("identity_dropped", path + ".title", "Missing intact protected product phrase: " + term)
        bullets = content.get("bullets")
        if not isinstance(bullets, list) or len(bullets) != limits["bullets_count"] or any(not nonempty(b) for b in _list(bullets)):
            add("bullets_shape", path + ".bullets", "Exactly five nonempty bullet strings are required")
        valid_bullets = [b for b in _list(bullets) if isinstance(b, str)]
        for index, bullet in enumerate(valid_bullets):
            label = re.fullmatch(r"【([^】]+)】\s*\S[\s\S]*", bullet)
            if not label or not label[1].strip() or (spaced_language and _has_lowercase(label[1])):
                add("bullet_format", path + ".bullets[%d]" % index,
                    "Use 【search phrase/topic label】 followed by natural target-language copy; preserve Japanese keyword and model spelling")
        editorial_bullets = list(valid_bullets)
        if policy.get("compatibility_lists", {}).get("exclude_verified_model_tokens_from_editorial_budget"):
            allowed = {"all"}
            for index, bullet in enumerate(valid_bullets):
                referenced = {fid for claim in _list(content.get("claims")) if isinstance(claim, dict)
                              and claim.get("field") == "bullets[%d]" % index
                              for fid in _list(claim.get("fact_ids")) if isinstance(fid, str)}
                models = [model for fid, (owner, fact) in facts.items() if fid in referenced and owner in allowed
                          and fact.get("field") == "compatible_models" and fact.get("status") == "confirmed"
                          and nonempty(fact.get("source_ref")) and valid_model_list(fact.get("value"))
                          for model in fact["value"]]
                pattern = model_pattern(models)
                if pattern:
                    editorial_bullets[index] = re.sub(pattern.pattern + r"[\s、,，;；/／・|]*", "", bullet, flags=re.I)
            if (editorial_bullets != valid_bullets and (any(len(b) > limits["bullet_max_chars"] for b in valid_bullets)
                    or sum(map(len, valid_bullets)) > limits["bullets_total_max_chars"])):
                add("compatibility_list_length", path + ".bullets",
                    "Verified model IDs are excluded only from local editorial budgets; full bullets remain unchanged for backend/category validation (raw %d chars, prose %d chars)"
                    % (sum(map(len, valid_bullets)), sum(map(len, editorial_bullets))), "warning")
        if any(len(b) > limits["bullet_max_chars"] for b in editorial_bullets) or sum(map(len, editorial_bullets)) > limits["bullets_total_max_chars"]:
            add("bullets_length", path + ".bullets", "Bullet or combined editorial budget exceeded")
        for index, bullet in enumerate(valid_bullets):
            if PROHIBITED_BULLET_SYMBOLS.search(bullet):
                add("bullet_prohibited_symbol", path + ".bullets[%d]" % index, "Amazon bullet guidelines disallow ™ ® © and emoji", "warning")
        for field in FRONT_FIELDS + ("search_terms",):
            value = _field(content, field)
            if isinstance(value, str):
                for term in forbidden:
                    if isinstance(term, str) and _contains(value, term):
                        add("forbidden_term", path + "." + field, "Contains a user-forbidden phrase: " + term)

    resolved = dict(resolved_listings(profile, listing))
    for target, content in resolved.items():
        check_text(str(target), content)

    def check_refs(record, allowed_owners, path):
        result = {"fact_ids": [], "measurement_ids": []}
        for key, collection in (("fact_ids", facts), ("measurement_ids", measurements)):
            for rid in array(record, key, path):
                source = collection.get(rid) if isinstance(rid, str) else None
                if source is None or source[0] not in allowed_owners:
                    add("reference_scope", path + "." + key, "Reference is missing or belongs to another product: " + str(rid))
                elif key == "fact_ids" and (source[1].get("status") != "confirmed" or not nonempty(source[1].get("source_ref"))):
                    add("unconfirmed_fact", path + "." + key, "Only confirmed, sourced facts can support claims")
                else:
                    result[key].append(rid)
        return result

    claim_refs, claim_fact_refs = {}, {}
    content_targets = dict(resolved)
    for target, content in content_targets.items():
        allowed = {"all"}
        found_fields = set()
        for index, claim in enumerate(array(content, "claims", "listing." + str(target))):
            path = "listing.%s.claims[%d]" % (target, index)
            if not isinstance(claim, dict):
                add("claim_object", path, "Claim must be an object")
                continue
            field = claim.get("field")
            valid_fields = FRONT_FIELDS + ("search_terms",)
            if field not in valid_fields:
                add("claim_field", path, "Claim field does not locate an applicable text field")
                continue
            found_fields.add(field)
            refs = check_refs(claim, allowed, path)
            claim_refs.setdefault((target, field), set()).update(refs["measurement_ids"])
            claim_fact_refs.setdefault((target, field), set()).update(refs["fact_ids"])
            value = _field(content, field)
            for mid in refs["measurement_ids"]:
                shown = measurements[mid][1].get("display_text")
                if not isinstance(value, str) or not isinstance(shown, str) or not _contains(value, shown):
                    add("measurement_text", path, "Text must reuse registered localized display_text for " + mid)
        required = FRONT_FIELDS
        for field in required:
            if field not in found_fields:
                add("claim_missing", "listing." + str(target) + "." + field, "Front-end field requires an explicit evidence/semantic-review claim record")
        # Scan only registered specifications. This cannot detect all invented numbers.
        for field in required:
            value = _field(content, field)
            if not isinstance(value, str):
                continue
            scan_measurements(value, allowed, "listing." + str(target) + "." + field)
            quantity_groups = scan_quantities(value, allowed, "listing." + str(target) + "." + field)
            if any(not group & claim_fact_refs.get((target, field), set()) for group in quantity_groups):
                add("quantity_unreferenced", "listing." + str(target) + "." + field, "Package count needs a corresponding quantity fact reference")
            for mid, (owner_id, measurement) in measurements.items():
                shown = measurement.get("display_text")
                if owner_id in allowed and isinstance(shown, str) and _contains(value, shown) and mid not in claim_refs.get((target, field), set()):
                    # Identical numeric dimensions may need only the specifically used label's ID.
                    same_value_ids = {rid for rid, (oid, rec) in measurements.items() if oid in allowed and rec.get("display_text") == shown}
                    if not same_value_ids & claim_refs.get((target, field), set()):
                        add("measurement_unreferenced", "listing." + str(target) + "." + field, "Registered specification is present without its measurement reference")
        bullet_fields = [("bullets[%d]" % index, text) for index, text in enumerate(_list(content.get("bullets"))) if isinstance(text, str)]
        expected_models = {}
        for fid, (owner, fact) in facts.items():
            if (owner not in allowed or fact.get("field") != "compatible_models"
                    or fact.get("status") != "confirmed" or not nonempty(fact.get("source_ref"))
                    or not valid_model_list(fact.get("value"))):
                continue
            for model in dict.fromkeys(fact["value"]):
                entry = expected_models.setdefault(normalize_model(model), {"model": model, "fact_ids": set()})
                entry["fact_ids"].add(fid)
        for entry in expected_models.values():
            model = entry["model"]
            pattern = model_pattern([model])
            locations = [field for field, text in bullet_fields if pattern.search(text)]
            if not locations:
                add("compatibility_model_missing", "listing." + str(target) + ".bullets",
                    "Keep this confirmed model intact in the bullet lists; a series summary, description-only mention or prefix is insufficient: " + model)
            elif not any(entry["fact_ids"] & claim_fact_refs.get((target, field), set()) for field in locations):
                add("compatibility_unreferenced", "listing." + str(target) + ".bullets",
                    "Model requires an applicable compatibility fact reference in the containing bullet: " + model)

    plan_ids, main_coverage = {}, set()

    def check_media_record(plan, path):
        applicable = scope(plan.get("applies_to"), path + ".applies_to")
        allowed = {"all"}
        refs = check_refs(plan, allowed, path)
        for key in ("buyer_question", "selling_point", "direction", "visual_evidence", "scene_intent", "mobile_check", "production_notes"):
            if not nonempty(plan.get(key)):
                add("plan_text", path + "." + key, "Planning rationale must be a nonempty string")
        status = plan.get("status", "ready")
        if status not in ("ready", "pending"):
            add("plan_status", path + ".status", "Plan status is ready or pending")
        elif status == "pending":
            add("plan_pending", path, "This creative still lacks facts or material; retain the production note and complete before acceptance", "incomplete")
        overlay = plan.get("overlay_text")
        if not isinstance(overlay, str):
            add("overlay_text", path + ".overlay_text", "overlay_text must be a string")
            overlay = ""
        native_text = plan.get("native_text", "")
        if not isinstance(native_text, str):
            add("native_text", path + ".native_text", "native_text must be a string")
            native_text = ""
        if plan.get("role") == "main" and (overlay or native_text):
            add("main_image_text", path + ".overlay_text", "Main image must not have overlaid marketing text")
        for key, value in (("overlay_text", overlay), ("native_text", native_text)):
            length_groups = scan_measurements(value, allowed, path + "." + key)
            if any(not group & set(refs["measurement_ids"]) for group in length_groups):
                add("measurement_unreferenced", path + "." + key, "Image length needs its corresponding measurement reference")
            quantity_groups = scan_quantities(value, allowed, path + "." + key)
            if any(not group & set(refs["fact_ids"]) for group in quantity_groups):
                add("quantity_unreferenced", path + "." + key, "Image count needs a corresponding quantity fact reference")
            if re.search(r"\{[^{}]+\}", value):
                add("unresolved_media_text", path + "." + key, "Resolved copy must contain actual product values, not template placeholders")
            for term in forbidden:
                if isinstance(term, str) and _contains(value, term):
                    add("forbidden_term", path + "." + key, "Contains a user-forbidden phrase: " + term)
        source_ids = array(plan, "source_image_ids", path)
        if not source_ids and plan.get("asset_type", "image") == "image":
            add("plan_material_pending", path + ".source_image_ids", "Image source is missing; identify required material in production_notes", "incomplete")
        for image_id in source_ids:
            source = images.get(image_id) if isinstance(image_id, str) else None
            if source is None or not applicable <= source[0]:
                add("plan_image_scope", path + ".source_image_ids", "Cannot reuse another product's or non-common material")
        body_refs = array(plan, "body_refs", path)
        if not body_refs:
            add("body_refs_empty", path + ".body_refs", "Plan must map to corresponding Listing text")
        for target in applicable:
            content = resolved.get(target, {})
            for field in body_refs:
                if field not in FRONT_FIELDS or not isinstance(_field(content, field), str):
                    add("body_ref", path + ".body_refs", "Body reference must locate existing front-end text for this product")
            for mid in refs["measurement_ids"]:
                shown = measurements[mid][1].get("display_text")
                if not any(mid in claim_refs.get((target, field), set()) and isinstance(_field(content, field), str)
                           and _contains(_field(content, field), shown or "") for field in body_refs if isinstance(field, str)):
                    add("plan_body_measurement", path, "Image specification must also appear and be referenced in the applicable Listing body")
            for fid in set(refs["fact_ids"]) & set(quantities):
                number = quantities[fid][1]
                if not any(fid in claim_fact_refs.get((target, field), set()) and any(
                        int(next(v for v in match.groups() if v is not None)) == number
                        for match in quantity_token.finditer(_field(content, field) or ""))
                        for field in body_refs if field in FRONT_FIELDS):
                    add("plan_body_quantity", path, "Image package quantity must also appear with its fact reference in the applicable body")
        if plan.get("role") != "main":
            for mid in refs["measurement_ids"]:
                shown = measurements[mid][1].get("display_text")
                if not isinstance(shown, str) or not (_contains(overlay, shown) or _contains(native_text, shown)):
                    add("plan_measurement_text", path, "Specification must reuse localized display_text in overlay_text or native_text")

    for plan_key in ("image_plan", a_plus_key):
        plans = array(listing, plan_key, "listing")
        if not plans:
            add("plan_empty", "listing." + plan_key, "A complete image/A+ plan is required")
        if plan_key == "image_plan" and not any(isinstance(p, dict) and p.get("role") == "main" for p in plans):
            add("main_image_missing", "listing.image_plan", "A main-image plan is required")
        for index, plan in enumerate(plans):
            path = "listing.%s[%d]" % (plan_key, index)
            if not isinstance(plan, dict):
                add("plan_object", path, "Plan must be an object")
                continue
            pid = plan.get("plan_id")
            if not nonempty(pid) or pid in plan_ids:
                add("plan_id", path, "Plan IDs must be nonempty and globally unique")
            else:
                plan_ids[pid] = plan
            applicable = scope(plan.get("applies_to"), path + ".applies_to")
            if plan_key == "image_plan" and plan.get("role") == "main":
                main_coverage.update(applicable)
            if plan.get("role") not in (("main", "secondary") if plan_key == "image_plan" else ("aplus",)):
                add("plan_role", path + ".role", "Role does not match its plan collection")
            if plan_key == a_plus_key:
                if plan.get("asset_type") not in ("image", "text") or not nonempty(plan.get("module")):
                    add("aplus_asset", path, "A+ needs its module label and explicit image/text asset_type")
            elif plan.get("asset_type", "image") != "image":
                add("plan_asset", path, "Main/secondary slots must be image creatives")
            check_media_record(plan, path)

    if main_coverage != {"single"}:
        add("main_image_coverage", "listing.image_plan", "The product needs an applicable main-image plan")
    check_media_plans(listing, add)

    # COSMO-style intent relations: structure and evidence are checked; expression is advisory.
    if "intent_map" in profile:
        resolved_texts = {target: " ".join(str(_field(content, field) or "") for field in FRONT_FIELDS + ("search_terms",))
                          for target, content in resolved.items()}
        for index, entry in enumerate(array(profile, "intent_map", "profile")):
            path = "profile.intent_map[%d]" % index
            if not isinstance(entry, dict) or entry.get("relation") not in INTENT_RELATIONS or not nonempty(entry.get("expression")):
                add("intent_map_entry", path, "Each intent needs a relation (%s) and a target-language expression" % "/".join(INTENT_RELATIONS))
                continue
            applicable = scope(entry.get("applies_to", ["all"]), path + ".applies_to")
            allowed = {"all"}
            check_refs({"fact_ids": entry.get("fact_ids", []), "measurement_ids": entry.get("measurement_ids", [])}, allowed, path)
            missing = sorted(t for t in applicable if not _contains(resolved_texts.get(t, ""), entry["expression"]))
            if missing:
                add("intent_unexpressed", path, "Confirmed intent is not stated in any indexed field for: " + ", ".join(missing), "warning")
    elif profile.get("schema_version") == "2.1":
        add("intent_map_missing", "profile.intent_map", "Map confirmed functions, audiences, places, occasions and companion products to listing text", "warning")
    if listing.get("schema_version") == "2.1":
        suggestions = listing.get("attribute_suggestions")
        if suggestions is None:
            add("attribute_suggestions_missing", "listing.attribute_suggestions", "Suggest backend structured attributes; Amazon says complete attributes help its shopping assistant", "warning")
        for index, item in enumerate(_list(suggestions)):
            path = "listing.attribute_suggestions[%d]" % index
            if not isinstance(item, dict) or not nonempty(item.get("attribute")) or item.get("value") in (None, "", []):
                add("attribute_suggestion", path, "Attribute suggestion needs an attribute name and a value")
                continue
            applicable = scope(item.get("applies_to", ["all"]), path + ".applies_to")
            allowed = {"all"}
            refs = check_refs({"fact_ids": item.get("fact_ids", []), "measurement_ids": item.get("measurement_ids", [])}, allowed, path)
            if not refs["fact_ids"] and not refs["measurement_ids"] and not item.get("fact_ids") and not item.get("measurement_ids"):
                add("attribute_unsupported", path, "Attribute values must cite confirmed facts or measurements")
        if len(_list(listing.get("buyer_question_coverage"))) < 6:
            add("question_matrix_thin", "listing.buyer_question_coverage", "Cover the who/what/when/where/why/how buyer-question matrix (mark missing facts as not_claimed)", "warning")

    if qa is not None:
        if not isinstance(qa, dict):
            add("qa_object", "qa", "QA must be an object")
        elif qa.get("site") != site:
            add("qa_site", "qa.site", "QA site must match profile")
        elif site != "US":
            if qa.get("status") != "skipped" or qa.get("reason_code") != "rufus_us_only" or qa.get("qa_pairs") != []:
                add("qa_non_us", "qa", "Non-US must retain the explicit skipped record with an empty qa_pairs array")
        else:
            if qa.get("status") == "unavailable":
                if not nonempty(qa.get("reason")) or any(qa.get(key) != [] for key in ("requested_keywords", "qa_pairs", "question_reviews")):
                    add("qa_unavailable_shape", "qa", "Unavailable QA requires the real reason and empty requested_keywords/qa_pairs/question_reviews arrays")
                else:
                    add("qa_unavailable", "qa", "US QA collection is unavailable; retain the reason and complete collection before final acceptance", "incomplete")
                return issues
            if qa.get("status") != "completed":
                add("qa_status", "qa.status", "US QA must be completed or the deliverable remains incomplete")
            requested = array(qa, "requested_keywords", "qa")
            if not requested or any(not nonempty(keyword) for keyword in requested):
                add("qa_requested_keywords", "qa.requested_keywords", "Retain the actual nonempty keyword strings used for collection")
            questions = set()
            for pair in array(qa, "qa_pairs", "qa"):
                if isinstance(pair, dict):
                    for question in _list(pair.get("questions")):
                        if isinstance(question, str):
                            questions.add((pair.get("keyword"), question))
            reviewed_questions = set()
            for index, review in enumerate(array(qa, "question_reviews", "qa")):
                path = "qa.question_reviews[%d]" % index
                if not isinstance(review, dict):
                    add("qa_review_object", path, "Question review must be an object")
                    continue
                pair = (review.get("keyword"), review.get("question"))
                if any(not isinstance(v, str) for v in pair):
                    add("qa_question", path, "Review requires original keyword and question strings")
                    continue
                if pair not in questions:
                    add("qa_provenance", path, "Reviewed question must come from the captured original response")
                if pair[0] not in requested:
                    quarantined = review.get("relevance") in ("irrelevant", "uncertain") and review.get("disposition") == "excluded"
                    add("qa_seed_mismatch", path, "Response seed differs from requested keywords; preserve and quarantine it",
                        "warning" if quarantined else "error")
                reviewed_questions.add(pair)
                relevance, disposition = review.get("relevance"), review.get("disposition")
                if relevance not in ("relevant", "irrelevant", "uncertain") or disposition not in ("answered", "unsupported", "excluded"):
                    add("qa_disposition", path, "Invalid relevance or disposition")
                if disposition == "answered" and (relevance != "relevant" or not _list(review.get("fact_ids"))):
                    add("qa_unsupported_answer", path, "Only relevant questions with product evidence may be answered")
                applicable = scope(review.get("applies_to"), path + ".applies_to")
                allowed = {"all"}
                check_refs({"fact_ids": review.get("fact_ids"), "measurement_ids": []}, allowed, path)
            if reviewed_questions != questions:
                add("qa_review_coverage", "qa.question_reviews", "Every captured question needs a relevance/disposition review")
    return issues


MEDIA_KEYS = ("image_plan", "a_plus_plan", "aplus_plan")


def target_fingerprints(profile, listing, qa=None):
    """Bind text and media review to the current product and complete copy."""
    require_single_product(profile, listing)
    text = {key: value for key, value in listing.items() if key not in MEDIA_KEYS}
    return {
        "single": canonical_sha256({"profile": profile, "listing": text, "qa": qa}),
        "media": canonical_sha256({"profile": profile, "media": {key: listing.get(key) for key in MEDIA_KEYS}, "content": text}),
    }


def make_review_template(profile_path, listing_path, qa_path=None, previous=None):
    """Pending checklist; with a previous review, unchanged targets keep their verdicts."""
    profile, listing = load_json(profile_path), load_json(listing_path)
    qa = load_json(qa_path) if qa_path else None
    policy = load_json(POLICY_PATH)
    targets = ["single"]
    current = target_fingerprints(profile, listing, qa)
    keyword_fps = keyword_review_fingerprints(Path(listing_path).parent)
    kept = {}
    required = {(target, check) for target in targets for check in policy["semantic_checks"]}
    required |= {("media", check) for check in policy["media_checks"]}
    if isinstance(previous, dict) and isinstance(previous.get("target_fingerprints"), dict):
        old_fps = previous["target_fingerprints"]
        keywords_same = previous.get("keyword_fingerprints") == keyword_fps
        for record in _list(previous.get("records")):
            if not isinstance(record, dict) or record.get("status") not in ("pass", "fail", "not_applicable"):
                continue
            target, check = str(record.get("target")), record.get("check")
            if ((target, check) in required and old_fps.get(target) == current.get(target)
                    and (check not in KEYWORD_REVIEW_CHECKS or keywords_same)):
                kept[(target, check)] = record
    records = [kept.get((target, check), {"target": target, "check": check, "status": "pending", "evidence": ""})
               for target in targets for check in policy["semantic_checks"]]
    records += [kept.get(("media", check), {"target": "media", "check": check, "status": "pending", "evidence": ""})
                for check in policy["media_checks"]]
    for record in records:
        guidance = policy.get("semantic_guidance", {}).get(record["check"])
        if guidance:
            record["guidance"] = guidance
    return {"schema_version": "2.1", "fingerprints": file_fingerprints(profile_path, listing_path, qa_path),
            "target_fingerprints": current,
            "keyword_fingerprints": keyword_fps,
            "carried_over": len(kept),
            "records": records,
            "limitations": "Review actual copy, facts, images, keyword_usage, field_layout and compatibility_models against 03_keyword_decisions/05_title_keywords; do not mechanically mark pass. Review actual used keywords and related intents, not every unused or excluded term. Check title traffic priority, pack quantity in highlights, all five bullet openings and material/size placement, as well as full-intent use, paraphrases and backend word order; references verify scope, not semantic truth."}


def keyword_review_fingerprints(run_dir):
    return {name: hashlib.sha256((Path(run_dir) / name).read_bytes()).hexdigest() if (Path(run_dir) / name).exists() else None
            for name in ("02_kw_raw.json", "02_identifier_roles.json", "03_keyword_decisions.json", "05_title_keywords.json")}


def current_keyword_stage(run_dir):
    spec = importlib.util.spec_from_file_location("listing_keyword_quality", Path(__file__).with_name("keyword_quality.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_run(Path(run_dir))


def validate_bundle(profile, listing, qa=None, review=None, backend=None, fingerprints=None, keywords=None):
    issues = check_local(profile, listing, qa)

    def add(code, path, message, severity="error"):
        issues.append({"severity": severity, "code": code, "path": path, "message": message})

    local_errors = sum(i["severity"] == "error" for i in issues)
    local_status = "failed" if local_errors else "incomplete" if any(i["severity"] == "incomplete" for i in issues) else "passed"
    if qa is None:
        add("qa_missing", "qa", "QA evidence or explicit non-US skipped file is missing", "incomplete")
    expected = load_json(POLICY_PATH)
    targets = ["single"]
    required = {(str(target), check) for target in targets for check in expected["semantic_checks"]}
    required |= {("media", check) for check in expected["media_checks"]}
    semantic_start = len(issues)
    if review is None:
        add("review_missing", "semantic", "Independent semantic review is missing", "incomplete")
    elif not isinstance(review, dict):
        add("review_invalid", "semantic", "Semantic review must be an object")
    else:
        stale_targets = set()
        if isinstance(review.get("target_fingerprints"), dict):
            current_targets = target_fingerprints(profile, listing, qa)
            stale_targets = {target for target, value in current_targets.items() if review["target_fingerprints"].get(target) != value}
            if stale_targets:
                add("review_stale", "semantic.target_fingerprints", "Content changed after review for: %s; rerun review-template to keep unchanged targets and re-review only these" % ", ".join(sorted(stale_targets)), "incomplete")
        elif fingerprints is None or review.get("fingerprints") != fingerprints:
            add("review_stale", "semantic.fingerprints", "Review is missing current source-file fingerprints", "incomplete")
        if keywords is not None and keywords.get("status") == "passed":
            keyword_bound = {name: keywords.get("fingerprints", {}).get(name) for name in ("02_kw_raw.json", "02_identifier_roles.json", "03_keyword_decisions.json", "05_title_keywords.json")}
            recorded_keywords = review.get("keyword_fingerprints")
            if not isinstance(recorded_keywords, dict) or any(name not in recorded_keywords or recorded_keywords[name] != value for name, value in keyword_bound.items()):
                add("review_keywords_stale", "semantic.keyword_fingerprints", "Keyword-usage and field-layout reviews are not bound to current keyword decisions and placements", "incomplete")
        seen = set()
        for index, record in enumerate(_list(review.get("records"))):
            path = "semantic.records[%d]" % index
            if not isinstance(record, dict):
                add("review_record", path, "Review record must be an object")
                continue
            key = (str(record.get("target")), str(record.get("check")))
            if key in seen or key not in required:
                add("review_target", path, "Duplicate or unexpected semantic target/check")
            seen.add(key)
            status = record.get("status")
            if status == "pending":
                add("review_pending", path, "Review still pending", "incomplete")
            elif status == "fail":
                add("review_failed", path, "Semantic review found a failure")
            elif status not in ("pass", "not_applicable"):
                add("review_status", path, "Invalid semantic review status")
            if not isinstance(record.get("evidence"), str) or not record["evidence"].strip():
                add("review_evidence", path, "Pass needs evidence and not_applicable needs a reason", "incomplete")
        if seen != required:
            add("review_coverage", "semantic.records", "Semantic review is missing one or more targets/checks", "incomplete")
    semantic_issues = issues[semantic_start:]
    semantic_status = "failed" if any(i["severity"] == "error" for i in semantic_issues) else "incomplete" if semantic_issues else "passed"
    backend_start = len(issues)
    try:
        payloads = {p["target"]: p for p in build_payloads(profile, listing)}
    except (ValueError, TypeError, AttributeError):
        payloads = {}
        add("payload_invalid", "backend", "Cannot project malformed content to backend payloads")
    if backend is None:
        add("backend_missing", "backend", "Backend validation is missing", "incomplete")
    elif not isinstance(backend, dict):
        add("backend_invalid", "backend", "Backend validation must be an object")
    else:
        seen = set()
        for index, record in enumerate(_list(backend.get("records"))):
            path = "backend.records[%d]" % index
            if not isinstance(record, dict):
                add("backend_record", path, "Backend record must be an object")
                continue
            target = record.get("target")
            if not isinstance(target, str) or target not in payloads or target in seen:
                add("backend_target", path, "Unexpected or duplicate backend target; only the current product is submitted")
                continue
            seen.add(target)
            if record.get("payload_sha256") != payloads[target]["payload_sha256"]:
                add("backend_stale", path, "Backend payload fingerprint is missing or stale", "incomplete")
            response = record.get("response")
            if record.get("exit_code") != 0 or type(record.get("exit_code")) is not int or not isinstance(response, dict) or response.get("ok") is not True or response.get("errors") != []:
                add("backend_failed", path, "Backend needs exit_code=0, JSON ok=true and errors=[]")
        if seen != set(payloads) or not payloads:
            add("backend_coverage", "backend.records", "Every sellable output requires its own current backend record", "incomplete")
    backend_issues = issues[backend_start:]
    backend_status = "failed" if any(i["severity"] == "error" for i in backend_issues) else "incomplete" if backend_issues else "passed"
    if keywords is None:
        keywords = {"status": "incomplete", "issues": [{"severity": "incomplete", "code": "keyword_audit_missing", "path": "keywords", "message": "Missing current keyword audit; legacy keyword files are not evidence of a completed review"}]}
    else:
        keywords = copy.deepcopy(keywords)
    if keywords.get("status") == "passed":
        bound = keywords.get("fingerprints", {})
        for source, key in (("01_product_profile.json", "profile_sha256"), ("listing.json", "listing_sha256"), ("06_qa.json", "qa_sha256")):
            if fingerprints is None or bound.get(source) != fingerprints.get(key):
                keywords["status"] = "incomplete"
                keywords.setdefault("issues", []).append({"severity": "incomplete", "code": "keyword_bundle_mismatch", "path": source, "message": "Keyword audit is not bound to the product, Listing or QA used by this validation"})
    issues.extend(keywords.get("issues", []))
    if keywords.get("status") != "passed" and not keywords.get("issues"):
        add("keyword_audit_incomplete", "keywords", "Keyword audit has not passed", "incomplete")
    status = "failed" if any(i["severity"] == "error" for i in issues) else "incomplete" if any(i["severity"] == "incomplete" for i in issues) else "passed"
    return {"schema_version": "2.1", "status": status, "fingerprints": fingerprints,
            "evidence_fingerprints": {"review_sha256": canonical_sha256(review) if review is not None else None,
                                      "backend_sha256": canonical_sha256(backend) if backend is not None else None,
                                      "keywords_sha256": canonical_sha256(keywords)},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "local": {"status": local_status, "error_count": local_errors},
            "semantic": {"status": semantic_status}, "backend": {"status": backend_status},
            "keywords": keywords,
            "issues": issues, "limitations": "Automated checks cover structure, registered values and reference scope; semantic review covers language, labels, factual truth, category eligibility and visual claims."}


def _backend_passed(record):
    return (isinstance(record, dict) and type(record.get("exit_code")) is int and record["exit_code"] == 0
            and isinstance(record.get("response"), dict) and record["response"].get("ok") is True
            and record["response"].get("errors") == [])


def run_backend(profile, listing, cli=None, timeout=120, config=None, previous=None):
    """Call CLI validate for this product; never print raw process IO.

    A previous passing record whose payload fingerprint is unchanged is reused, so a
    unchanged product copy does not need another request.
    """
    issues = check_local(profile, listing)
    if any(issue["severity"] == "error" for issue in issues):
        return {"schema_version": "2.1", "status": "failed", "issues": issues, "records": []}
    reusable = {r.get("target"): r for r in _list(_dict(previous).get("records")) if _backend_passed(r)}
    records = []
    for payload in build_payloads(profile, listing):
        old = reusable.get(payload["target"])
        if old is not None and old.get("payload_sha256") == payload["payload_sha256"]:
            records.append(dict(old, reused=True))
            continue
        with tempfile.TemporaryDirectory(prefix="listing-validate-") as directory:
            path = Path(directory) / payload["filename"]
            atomic_write_json(path, payload["payload"])
            record = {"target": payload["target"], "payload_sha256": payload["payload_sha256"], "reused": False}
            record.update(backend_cli.run_cli("validate", site=profile["site"], listing_file=path,
                                              cli=cli, config=config, timeout=timeout))
            records.append(record)
    return {"schema_version": "2.1", "records": records}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("normalize", "check", "prepare", "review-template", "backend"):
        command = commands.add_parser(name)
        command.add_argument("--profile", required=True)
        if name != "normalize":
            command.add_argument("--listing", required=True)
        if name in ("check", "review-template"):
            command.add_argument("--qa")
        if name == "check":
            command.add_argument("--review")
            command.add_argument("--backend")
        if name == "review-template":
            command.add_argument("--fresh", action="store_true", help="Ignore an existing review instead of keeping verdicts for unchanged targets")
        if name == "backend":
            command.add_argument("--no-reuse", action="store_true", help="Re-validate this product even when its payload and passing result are unchanged")
            command.add_argument("--cli", help="Optional CLI path; defaults to the bundled platform executable")
            command.add_argument("--config", help="Optional config path; defaults to ~/.amazon-skill/config.json")
            command.add_argument("--timeout", type=float, default=120)
        if name == "prepare":
            command.add_argument("--output-dir", required=True)
        else:
            command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        profile = load_json(args.profile)
        listing = load_json(args.listing) if args.command != "normalize" else None
        if args.command == "normalize":
            atomic_write_json(args.output, normalize_profile(profile))
            print("normalized")
            return 0
        if args.command == "review-template":
            previous = None
            if not args.fresh and Path(args.output).exists():
                try:
                    previous = load_json(args.output)
                except ValueError:
                    previous = None
            template = make_review_template(args.profile, args.listing, args.qa, previous)
            atomic_write_json(args.output, template)
            pending = sum(r["status"] == "pending" for r in template["records"])
            print("semantic review template written: %d kept from unchanged targets, %d pending" % (template["carried_over"], pending))
            return 0
        if args.command == "check":
            result = validate_bundle(profile, listing, load_json(args.qa) if args.qa else None,
                                     load_json(args.review) if args.review else None,
                                     load_json(args.backend) if args.backend else None,
                                     file_fingerprints(args.profile, args.listing, args.qa),
                                     keywords=current_keyword_stage(Path(args.listing).parent))
            atomic_write_json(args.output, result)
            print(result["status"])
            return 0 if result["status"] == "passed" else 1
        issues = check_local(profile, listing)
        if any(i["severity"] == "error" for i in issues):
            result = {"status": "failed", "issues": issues, "records": []}
            path = Path(args.output_dir) / "manifest.json" if args.command == "prepare" else args.output
            atomic_write_json(path, result)
            print("failed: correct local issues before payload preparation/backend validation")
            return 1
        if args.command == "prepare":
            payloads = build_payloads(profile, listing)
            for payload in payloads:
                atomic_write_json(Path(args.output_dir) / payload["filename"], payload["payload"])
            atomic_write_json(Path(args.output_dir) / "manifest.json", {"schema_version": "2.1", "status": "prepared",
                              "fingerprints": file_fingerprints(args.profile, args.listing),
                              "records": [{k: v for k, v in p.items() if k != "payload"} for p in payloads]})
            print("prepared %d sellable payloads" % len(payloads))
            return 0
        previous = None
        if not args.no_reuse and Path(args.output).exists():
            try:
                previous = load_json(args.output)
            except ValueError:
                previous = None
        result = run_backend(profile, listing, args.cli, timeout=args.timeout, config=args.config, previous=previous)
        atomic_write_json(args.output, result)
        valid = bool(result["records"]) and all(_backend_passed(r) for r in result["records"])
        reused = sum(bool(r.get("reused")) for r in result["records"])
        print("backend %s (%d requested, %d reused)" % ("passed" if valid else "failed", len(result["records"]) - reused, reused))
        return 0 if valid else 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        # Do not print user file contents, environment values, or raw CLI exception IO.
        print("error: %s; inspect input JSON, units, required fields, and paths" % type(error).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
