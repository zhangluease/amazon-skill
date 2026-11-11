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


def configure(root, payload, legacy_paths=()):
    """Merge credential updates atomically; preserve unrelated config fields."""
    root = Path(root)
    path = root / "config.json"
    if (not isinstance(payload, dict) or not payload
            or set(payload) - {"backend_token", "aba_mcp"}):
        raise ValueError("EXPECTED_BACKEND_TOKEN_OR_ABA_MCP")
    if "backend_token" in payload:
        validate_token(payload["backend_token"])
    if "aba_mcp" in payload:
        aba = payload["aba_mcp"]
        if (not isinstance(aba, dict) or "access_token" not in aba
                or set(aba) - {"url", "access_token"}):
            raise ValueError("EXPECTED_ABA_MCP_ACCESS_TOKEN")
        validate_token(aba["access_token"], bearer=True)
        if aba.get("url", ABA_URL) != ABA_URL:
            raise ValueError("ABA_MCP_URL_INVALID")

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
    if "aba_mcp" in payload:
        previous = config.get("aba_mcp", {})
        if not isinstance(previous, dict):
            raise ValueError("ABA_MCP_CONFIG_INVALID")
        config["aba_mcp"] = {**previous, "url": ABA_URL, **payload["aba_mcp"]}

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
    aba = config.get("aba_mcp")
    if not isinstance(aba, dict) or aba.get("url") != ABA_URL:
        raise ValueError("ABA_MCP_CONFIG_INVALID")
    token = validate_token(aba.get("access_token"), bearer=True)
    return {"Authorization": "Bearer " + token}


def config_status(root=STATE):
    path = Path(root) / "config.json"
    config = read_config(path) if path.exists() or path.is_symlink() else {}
    aba = config.get("aba_mcp", {})
    return {"config_file": str(path), "exists": path.is_file(),
            "backend_token_configured": bool(config.get("backend_token")),
            "aba_mcp_access_token_configured": isinstance(aba, dict) and bool(aba.get("access_token"))}


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
