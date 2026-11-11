#!/usr/bin/env python3
"""Manage this skill's local credentials without displaying credential values."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

STATE = Path.home() / ".amazon-jp-listing-asin"
ABA_URL = "https://spapi-mcp.bamboost.info/mcp"
BACKEND_URL = "https://mcp.yixunkuajing.com"
ABA_CONFIG_KEYS = ("aba_mcp", "spapi_mcp")


def read_config(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("CONFIG_SYMLINK_REFUSED")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("CONFIG_OBJECT_REQUIRED")
    return data


def validate_token(value, *, bearer=False):
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or (bearer and any(character.isspace() for character in value))):
        raise ValueError("INVALID_TOKEN")
    return value


def aba_settings(config):
    """Accept the historical field spelling without silently choosing conflicting tokens."""
    candidates = []
    for name in ABA_CONFIG_KEYS:
        if name not in config:
            continue
        section = config[name]
        if not isinstance(section, dict):
            raise ValueError("ABA_MCP_CONFIG_INVALID")
        token = section.get("access_token")
        if token is None or token == "":
            continue
        validate_token(token, bearer=True)
        if section.get("url", ABA_URL) != ABA_URL:
            raise ValueError("ABA_MCP_URL_INVALID")
        candidates.append((name, token))
    if len({token for _name, token in candidates}) > 1:
        raise ValueError("ABA_MCP_CONFIG_CONFLICT")
    name, token = candidates[0] if candidates else (None, None)
    return {"url": ABA_URL, "access_token": token, "source": name}


def unify_aba_config(config, update=None):
    previous = {}
    for name in reversed(ABA_CONFIG_KEYS):
        section = config.get(name, {})
        if not isinstance(section, dict):
            raise ValueError("ABA_MCP_CONFIG_INVALID")
        previous.update(section)
    if update is not None:
        previous.update(update)
    else:
        settings = aba_settings(config)
        if settings["access_token"] is not None:
            previous["access_token"] = settings["access_token"]
    previous["url"] = ABA_URL
    config["aba_mcp"] = previous
    config.pop("spapi_mcp", None)


def configure(root, payload, legacy_paths=()):
    """Merge credential updates atomically; preserve unrelated config fields."""
    root = Path(root)
    path = root / "config.json"
    if (not isinstance(payload, dict) or not payload
            or set(payload) - {"backend_token", *ABA_CONFIG_KEYS}):
        raise ValueError("EXPECTED_BACKEND_TOKEN_OR_ABA_MCP")
    if "backend_token" in payload:
        validate_token(payload["backend_token"])
    for name in ABA_CONFIG_KEYS:
        if name not in payload:
            continue
        aba = payload[name]
        if (not isinstance(aba, dict) or "access_token" not in aba
                or set(aba) - {"url", "access_token"}):
            raise ValueError("EXPECTED_ABA_MCP_ACCESS_TOKEN")
        validate_token(aba["access_token"], bearer=True)
        if aba.get("url", ABA_URL) != ABA_URL:
            raise ValueError("ABA_MCP_URL_INVALID")
    if any(name in payload for name in ABA_CONFIG_KEYS):
        aba_settings(payload)
        update = {}
        for name in reversed(ABA_CONFIG_KEYS):
            update.update(payload.get(name, {}))
        payload = {key: value for key, value in payload.items() if key not in ABA_CONFIG_KEYS}
        payload["aba_mcp"] = update

    config = read_config(path) if path.exists() or path.is_symlink() else {}
    # Creating an ABA-only primary file must not hide a working legacy backend token.
    if not config.get("backend_token"):
        for legacy_path in legacy_paths:
            candidate = Path(legacy_path)
            if not candidate.is_file() or candidate.resolve() == path.resolve():
                continue
            legacy = read_config(candidate)
            if legacy.get("backend_token"):
                legacy_url = legacy.get("backend_url", BACKEND_URL)
                if config.get("backend_url") and config["backend_url"] != legacy_url:
                    continue
                config = {**legacy, **config, "backend_token": legacy["backend_token"]}
                break
    config.setdefault("backend_url", BACKEND_URL)
    config.setdefault("backend_token", "")
    if "backend_token" in payload:
        config["backend_token"] = payload["backend_token"]
    if "aba_mcp" in payload or any(name in config for name in ABA_CONFIG_KEYS):
        unify_aba_config(config, payload.get("aba_mcp"))

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        root.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"status": "completed", "config_file": str(path), "updated": sorted(payload)}


def aba_headers(root=STATE):
    config = read_config(Path(root) / "config.json")
    aba = aba_settings(config)
    if aba["access_token"] is None:
        raise ValueError("ABA_MCP_ACCESS_TOKEN_MISSING")
    token = aba["access_token"]
    return {"Authorization": "Bearer " + token}


def normalize_config(root=STATE, legacy_paths=()):
    settings = aba_settings(read_config(Path(root) / "config.json"))
    if settings["access_token"] is None:
        raise ValueError("ABA_MCP_ACCESS_TOKEN_MISSING")
    return configure(root, {"aba_mcp": {"access_token": settings["access_token"]}}, legacy_paths)


def config_status(root=STATE):
    path = Path(root) / "config.json"
    config = read_config(path) if path.exists() or path.is_symlink() else {}
    error = None
    try:
        settings = aba_settings(config)
    except ValueError as failure:
        error = str(failure)
        settings = {"access_token": None, "source": None}
    return {"config_file": str(path), "exists": path.is_file(),
            "backend_token_configured": bool(config.get("backend_token")),
            "aba_mcp_access_token_configured": settings["access_token"] is not None,
            "aba_mcp_config_source": settings["source"],
            "aba_mcp_config_error": error}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "headers"), nargs="?", default="status")
    args = parser.parse_args(argv)
    try:
        result = aba_headers() if args.command == "headers" else config_status()
    except (OSError, ValueError, UnicodeError):
        # Headers go only to the MCP client. Failure output never includes config content.
        print("SKILL_CONFIG_UNAVAILABLE_OR_INVALID", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
