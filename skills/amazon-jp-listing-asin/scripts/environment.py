#!/usr/bin/env python3
"""Prepare local keyword research dependencies and run Japan ASIN expansion."""

import argparse
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
import platform
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen


SKILL = Path(__file__).resolve().parents[1]
STATE = Path.home() / ".amazon-jp-listing-asin"
UPSTREAM = "lc-amazon-listing-asin"
REVISION = "87dead1472eb4ddfd28f99fd47a9593b5c3f7771"
SOURCE = f"https://raw.githubusercontent.com/chenqinglin2019-tech/Laochen-Amazon-project/{REVISION}/skills/{UPSTREAM}"
BACKEND = "scripts/backend_cli.py"
CREDENTIALS = "scripts/configure_credentials.py"
CHECKSUMS = {
    BACKEND: "8eaa03c831f3a32030f1a81f62cfd9dd4fd7bea1cccd97809d97380c6743fd24",
    CREDENTIALS: "b93fb06e2f259a82ac19d459b03e9d3a58db98243b3d3487cf5c180dc167948f",
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


class SetupError(Exception):
    """Report a setup failure without printing raw dependency or credential data."""


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


def expand(args):
    system = platform.system().lower()
    machine = platform.machine().lower()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    suffix = CLI_SUFFIXES.get((system, architecture))
    if suffix is None:
        raise SetupError("ASIN lookup supports macOS Intel/Apple Silicon and Linux/Windows x64.")
    backend = ensure_asset(BACKEND)
    cli = ensure_asset(f"tools/bin/laochen-cli-{suffix}")
    return subprocess.call([
        sys.executable, str(backend), "expand", "--site", "JP",
        "--asins", args.asins, "--output", args.output,
        "--reuse-days", str(args.reuse_days), "--cli", str(cli),
        "--config", str(config_path()),
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    lookup = commands.add_parser("expand", help="Look up Japan ASINs")
    lookup.add_argument("--asins", required=True)
    lookup.add_argument("--output", required=True)
    lookup.add_argument("--reuse-days", type=float, default=7)
    commands.add_parser("configure", help="Save a backend token supplied as JSON on stdin")
    args = parser.parse_args()
    try:
        if args.command == "expand":
            return expand(args)
        configure = runpy.run_path(str(ensure_asset(CREDENTIALS)))["configure"]
        configure(STATE, json.load(sys.stdin))
    except SetupError as error:
        print(json.dumps({"status": "failed", "error_code": "setup_failed", "message": str(error)}))
        return 1
    except (OSError, ValueError):
        message = "Could not save the local token." if args.command == "configure" else "Could not prepare the ASIN lookup."
        print(json.dumps({"status": "failed", "error_code": "local_setup_failed", "message": message}))
        return 1
    print('{"status":"completed"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
