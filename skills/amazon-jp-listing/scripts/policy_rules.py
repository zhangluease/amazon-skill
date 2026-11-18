"""Fingerprint rules that affect validation or content review, excluding dates."""
import hashlib
import json


def rules_fingerprint(policy):
    keys = ("default_limits", "editorial_targets", "sites", "semantic_checks", "semantic_guidance", "media_checks",
            "media_limits", "title_compliance", "compatibility_lists", "keyword_inputs")
    rules = {key: policy.get(key) for key in keys}
    raw = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
