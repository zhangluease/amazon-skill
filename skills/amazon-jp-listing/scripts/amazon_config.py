#!/usr/bin/env python3
"""Shared Amazon skill configuration; credentials enter through standard input."""

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

STATE = Path.home() / ".amazon-skill"
BACKEND_URL = "https://mcp.yixunkuajing.com"
ABA_URL = "https://spapi-mcp.bamboost.info/mcp"
DEFAULT_CONFIG = {"backend_url": BACKEND_URL, "backend_token": "",
                  "aba_mcp": {"url": ABA_URL, "access_token": ""}}


def config_path():
    return STATE / "config.json"


def read_config(path):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("CONFIG_SYMLINK_REFUSED")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("CONFIG_OBJECT_REQUIRED")
    return data


def validate_token(value, *, bearer=False):
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or (bearer and any(char.isspace() for char in value))):
        raise ValueError("INVALID_TOKEN")
    return value


def validate_backend_url(value):
    try:
        parsed = urlsplit(value)
        valid = (isinstance(value, str) and value == value.strip()
                 and not any(ord(char) < 32 or ord(char) == 127 for char in value)
                 and parsed.scheme in ("https", "http") and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment)
        parsed.port
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("BACKEND_URL_INVALID")
    return value


def aba_settings(config):
    section = config.get("aba_mcp", {})
    if not isinstance(section, dict):
        raise ValueError("ABA_MCP_CONFIG_INVALID")
    url = section.get("url", ABA_URL)
    if url != ABA_URL:
        raise ValueError("ABA_MCP_URL_INVALID")
    token = section.get("access_token", "")
    if token != "":
        validate_token(token, bearer=True)
    return {"url": url, "access_token": token or None, "source": "aba_mcp" if token else None}


def _write_config(root, config):
    root = Path(root)
    path = root / "config.json"
    if root.is_symlink() or path.is_symlink():
        raise ValueError("CONFIG_SYMLINK_REFUSED")
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
    return path


def init_config(root=STATE):
    path = Path(root) / "config.json"
    if path.exists() or path.is_symlink():
        read_config(path)
        return {"status": "exists", "config_file": str(path)}
    _write_config(root, copy.deepcopy(DEFAULT_CONFIG))
    return {"status": "initialized", "config_file": str(path)}


def configure(root, payload):
    """Update supplied service fields once for every installed Amazon skill."""
    if (not isinstance(payload, dict) or not payload
            or set(payload) - {"backend_url", "backend_token", "aba_mcp"}):
        raise ValueError("EXPECTED_SHARED_SERVICE_FIELDS")
    if "backend_url" in payload:
        validate_backend_url(payload["backend_url"])
    if "backend_token" in payload and payload["backend_token"] != "":
        validate_token(payload["backend_token"])
    if "aba_mcp" in payload:
        section = payload["aba_mcp"]
        if (not isinstance(section, dict) or "access_token" not in section
                or set(section) - {"url", "access_token"}):
            raise ValueError("EXPECTED_ABA_MCP_ACCESS_TOKEN")
        aba_settings(payload)
    root = Path(root)
    path = root / "config.json"
    config = read_config(path) if path.exists() or path.is_symlink() else copy.deepcopy(DEFAULT_CONFIG)
    old_url = config.get("backend_url", BACKEND_URL)
    if payload.get("backend_url", old_url) != old_url and "backend_token" not in payload:
        raise ValueError("BACKEND_URL_CHANGE_REQUIRES_TOKEN")
    for field in ("backend_url", "backend_token"):
        if field in payload:
            config[field] = payload[field]
    if "aba_mcp" in payload:
        previous = config.get("aba_mcp", {})
        if not isinstance(previous, dict):
            raise ValueError("ABA_MCP_CONFIG_INVALID")
        config["aba_mcp"] = {**previous, **payload["aba_mcp"]}
        config["aba_mcp"].setdefault("url", ABA_URL)
    _write_config(root, config)
    return {"status": "completed", "config_file": str(path), "updated": sorted(payload)}


def aba_headers(root=STATE):
    settings = aba_settings(read_config(Path(root) / "config.json"))
    if settings["access_token"] is None:
        raise ValueError("ABA_MCP_ACCESS_TOKEN_MISSING")
    return {"Authorization": "Bearer " + settings["access_token"]}


def config_status(root=STATE):
    path = Path(root) / "config.json"
    config = read_config(path) if path.exists() or path.is_symlink() else {}
    backend_error = aba_error = None
    backend_configured = False
    try:
        validate_backend_url(config.get("backend_url", BACKEND_URL))
        token = config.get("backend_token", "")
        if token != "":
            validate_token(token)
            backend_configured = True
    except ValueError as error:
        backend_error = str(error)
    try:
        settings = aba_settings(config)
    except ValueError as error:
        aba_error = str(error)
        settings = {"access_token": None, "source": None}
    return {"config_file": str(path), "exists": path.is_file(),
            "backend_token_configured": backend_configured, "backend_config_error": backend_error,
            "aba_mcp_access_token_configured": settings["access_token"] is not None,
            "aba_mcp_config_source": settings["source"], "aba_mcp_config_error": aba_error}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "status", "configure", "headers"), nargs="?", default="status")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = init_config()
        elif args.command == "configure":
            result = configure(STATE, json.load(sys.stdin))
        elif args.command == "headers":
            result = aba_headers()
        else:
            result = config_status()
    except (OSError, ValueError, TypeError, UnicodeError):
        print("AMAZON_CONFIG_UNAVAILABLE_OR_INVALID", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
