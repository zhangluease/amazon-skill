#!/usr/bin/env python3
"""Prepare local keyword research dependencies and run Japan ASIN expansion."""

import argparse
from hashlib import sha256
from importlib import import_module
import json
import math
from pathlib import Path
import platform
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.request import urlopen

from skill_config import configure as configure_credentials, config_status


SKILL = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".amazon-jp-listing-asin"
UPSTREAM = "lc-amazon-listing-asin"
REVISION = "87dead1472eb4ddfd28f99fd47a9593b5c3f7771"
SOURCE = f"https://raw.githubusercontent.com/chenqinglin2019-tech/Laochen-Amazon-project/{REVISION}/skills/{UPSTREAM}"
BACKEND = "scripts/backend_cli.py"
CHECKSUMS = {
    BACKEND: "8eaa03c831f3a32030f1a81f62cfd9dd4fd7bea1cccd97809d97380c6743fd24",
    "tools/bin/laochen-cli-darwin-amd64": "123350fd5b7ec8838fe46a3202fa5b791e85e5e2b77503de803e8c381602d5b6",
    "tools/bin/laochen-cli-darwin-arm64": "bde4a26fed7940a7a8d1a05cc0ee8da620251c48513488ea07daf70aa5658e97",
    "tools/bin/laochen-cli-linux-amd64": "80740278458d2ec2f0861e7885d2a5d12790f0edfb53f692b2ccdaf000eb89e3",
    "tools/bin/laochen-cli-windows-amd64.exe": "006f7d1c168049a679cb9ba139c312b212b158b5bbabfea1c093f982a4606dac",
}
CLI_SUFFIXES = {
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "amd64"): "darwin-amd64",
    ("linux", "amd64"): "linux-amd64",
    ("windows", "amd64"): "windows-amd64.exe",
}
EXCEL_DEPENDENCY = "openpyxl==3.1.5"
EXPAND_META_SUFFIX = ".meta.json"


class SetupError(Exception):
    """Report a setup failure without printing raw dependency or credential data."""


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".json-", dir=path.parent) as directory:
        staging = Path(directory) / path.name
        staging.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        staging.chmod(0o600)
        staging.replace(path)


def prepare_excel():
    try:
        import_module("openpyxl")
        return
    except ModuleNotFoundError as error:
        if error.name != "openpyxl":
            raise SetupError("The current Python has an incomplete openpyxl installation.") from None

    packages = STATE / "python" / EXCEL_DEPENDENCY.replace("==", "-")
    if not packages.is_dir():
        packages.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with TemporaryDirectory(prefix=".excel-", dir=packages.parent) as directory:
            staging = Path(directory) / "packages"
            command = [sys.executable, "-m", "pip", "install", "--quiet",
                       "--disable-pip-version-check", "--no-input", "--only-binary=:all:",
                       "--target", str(staging), EXCEL_DEPENDENCY]
            try:
                subprocess.run(command, check=True, timeout=120,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                raise SetupError("Could not prepare Excel export. Check pip and package network access.") from None
            try:
                staging.rename(packages)
            except OSError:
                if not packages.is_dir():
                    raise
    sys.path.insert(0, str(packages))


def upstream_locations():
    return (SKILL.parent / UPSTREAM, Path.cwd() / "skills" / UPSTREAM)


def ensure_asset(relative_path):
    destination = STATE / "laochen" / REVISION / relative_path
    expected = CHECKSUMS[relative_path]
    if destination.is_file() and sha256(destination.read_bytes()).hexdigest() == expected:
        return destination

    for location in upstream_locations():
        candidate = location / relative_path
        if candidate.is_file():
            data = candidate.read_bytes()
            if sha256(data).hexdigest() == expected:
                break
    else:
        try:
            with urlopen(f"{SOURCE}/{relative_path}", timeout=30) as response:
                data = response.read()
        except OSError:
            raise SetupError("Could not download the ASIN lookup dependency. Check GitHub access.") from None
        if sha256(data).hexdigest() != expected:
            raise SetupError("The downloaded ASIN lookup dependency failed integrity verification.")

    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with TemporaryDirectory(prefix=".asset-", dir=destination.parent) as directory:
        staging = Path(directory) / destination.name
        staging.write_bytes(data)
        if relative_path.startswith("tools/bin/"):
            staging.chmod(0o700)
        staging.replace(destination)
    return destination


def config_path():
    config = STATE / "config.json"
    if config.exists():
        return config
    return next((path for location in upstream_locations()
                 if (path := location / "config.json").is_file()), config)


def cacheable_expand(response):
    if not isinstance(response, dict):
        return False
    if (response.get("ok") is False or response.get("success") is False
            or response.get("error") or response.get("errors") or response.get("partial") is True):
        return False
    status = str(response.get("status", "")).strip().lower()
    if status in {"failed", "error", "cancelled", "canceled", "partial", "pending", "running", "queued", "in_progress"}:
        return False
    raw = response.get("raw")
    return (isinstance(response.get("keywords"), list)
            or isinstance(raw, dict) and isinstance(raw.get("keyword_data"), (list, dict)))


def reject_nonfinite(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def find_reusable_expand(output, asins, days):
    best = None
    now = time.time()
    for meta_path in output.parent.parent.glob("*/02_kw_raw.json" + EXPAND_META_SUFFIX):
        raw_path = meta_path.with_name("02_kw_raw.json")
        if raw_path.resolve() == output:
            continue
        try:
            meta_bytes = meta_path.read_bytes()
            meta = json.loads(meta_bytes, parse_constant=reject_nonfinite)
            if (not isinstance(meta, dict) or meta.get("site") != "JP" or meta.get("asins") != asins
                    or meta.get("reused_from")):
                continue
            fetched = meta.get("fetched_at_epoch")
            if type(fetched) not in (int, float) or not now - days * 86400 <= fetched <= now:
                continue
            if best is not None and fetched <= best[2]["fetched_at_epoch"]:
                continue
            raw_bytes = raw_path.read_bytes()
            digest = meta.get("response_sha256")
            if digest is not None and digest != sha256(raw_bytes).hexdigest():
                continue
            response = json.loads(raw_bytes, parse_constant=reject_nonfinite)
            if cacheable_expand(response) and meta_bytes == meta_path.read_bytes():
                best = (raw_path, response, meta)
        except (OSError, ValueError):
            continue
    return best


def expand(args):
    if not math.isfinite(args.reuse_days) or args.reuse_days < 0:
        raise SetupError("Cache reuse days must be a finite nonnegative number.")
    system = platform.system().lower()
    machine = platform.machine().lower()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    suffix = CLI_SUFFIXES.get((system, architecture))
    if suffix is None:
        raise SetupError("ASIN lookup supports macOS Intel/Apple Silicon and Linux/Windows x64.")
    output = Path(args.output).resolve()
    config = config_path().resolve()
    cli_asset = f"tools/bin/laochen-cli-{suffix}"
    asset_root = STATE / "laochen" / REVISION
    if output in (config, (asset_root / BACKEND).resolve(), (asset_root / cli_asset).resolve()):
        raise SetupError("Output must not overwrite the config or an ASIN lookup dependency.")
    meta_path = output.with_name(output.name + EXPAND_META_SUFFIX)
    asins = sorted({asin.strip().upper() for asin in args.asins.split(",") if asin.strip()})
    cached = find_reusable_expand(output, asins, args.reuse_days) if args.reuse_days else None
    if cached is not None:
        raw_path, response, meta = cached
        meta_path.unlink(missing_ok=True)
        write_json(output, response)
        source = raw_path.parent.name
        write_json(meta_path, dict(meta, reused_from=source))
        print(json.dumps({
            "command": "expand", "status": "completed", "exit_code": 0, "reused_from": source,
        }, ensure_ascii=False))
        return 0

    backend = ensure_asset(BACKEND)
    cli = ensure_asset(cli_asset)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep upstream metadata private until the response passes the business checks.
    with TemporaryDirectory(prefix=".expand-", dir=output.parent) as directory:
        staging = Path(directory) / output.name
        completed = subprocess.run([
            sys.executable, str(backend), "expand", "--site", "JP",
            "--asins", args.asins, "--output", str(staging), "--reuse-days", "0",
            "--cli", str(cli), "--config", str(config),
        ], stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if staging.is_file():
            response_bytes = staging.read_bytes()
            meta_path.unlink(missing_ok=True)
            staging.replace(output)
            if completed.returncode == 0 and cacheable_expand(json.loads(response_bytes)):
                write_json(meta_path, {
                    "asins": asins, "site": "JP", "fetched_at_epoch": time.time(),
                    "response_sha256": sha256(response_bytes).hexdigest(),
                })
    print(completed.stdout, end="")
    return completed.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    lookup = commands.add_parser("expand", help="Look up Japan ASINs")
    lookup.add_argument("--asins", required=True)
    lookup.add_argument("--output", required=True)
    lookup.add_argument("--reuse-days", type=float, default=7)
    commands.add_parser("configure", help="Merge backend_token and/or aba_mcp credentials from JSON on stdin")
    commands.add_parser("config-status", help="Show credential presence and the local config path without token values")
    args = parser.parse_args()
    try:
        if args.command == "expand":
            return expand(args)
        if args.command == "config-status":
            print(json.dumps(config_status(STATE), ensure_ascii=False))
            return 0
        result = configure_credentials(STATE, json.load(sys.stdin),
                                       legacy_paths=[location / "config.json" for location in upstream_locations()])
    except SetupError as error:
        print(json.dumps({"status": "failed", "error_code": "setup_failed", "message": str(error)}))
        return 1
    except (OSError, ValueError):
        message = "Could not read or update the local config." if args.command != "expand" else "Could not prepare the ASIN lookup."
        print(json.dumps({"status": "failed", "error_code": "local_setup_failed", "message": message}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
