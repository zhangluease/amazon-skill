#!/usr/bin/env python3
"""Initialize a Japanese Listing run and prepare the pinned validation CLI."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from listing_config import STATE, configure, config_status

SKILL = Path(__file__).resolve().parents[1]
MANIFEST = SKILL / "references" / "backend-assets.json"
PLATFORMS = {
    ("darwin", "arm64"): "darwin-arm64",
    ("darwin", "aarch64"): "darwin-arm64",
    ("darwin", "x86_64"): "darwin-amd64",
    ("darwin", "amd64"): "darwin-amd64",
    ("linux", "x86_64"): "linux-amd64",
    ("linux", "amd64"): "linux-amd64",
    ("windows", "amd64"): "windows-amd64.exe",
    ("windows", "x86_64"): "windows-amd64.exe",
}


class SetupError(Exception):
    """A setup error whose message contains no credentials or response body."""


def config_path():
    return STATE / "config.json"


def prepare_backend_cli():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    suffix = PLATFORMS.get((platform.system().lower(), platform.machine().lower()))
    if suffix is None:
        raise SetupError("No validation CLI is available for this OS/architecture.")
    name = "laochen-cli-v2-" + suffix
    expected = manifest["sha256"][name]
    revision = manifest["revision"]
    destination = STATE / "cache" / "listing" / "laochen" / revision / name
    if destination.is_file() and sha256(destination.read_bytes()).hexdigest() == expected:
        return destination
    url = ("https://raw.githubusercontent.com/chenqinglin2019-tech/Laochen-Amazon-project/"
           + revision + "/" + manifest["skill_path"] + "/tools/bin/" + name)
    try:
        with urlopen(url, timeout=30) as response:
            data = response.read(32 * 1024 * 1024 + 1)
    except OSError:
        raise SetupError("Could not download the validation CLI; check GitHub connectivity.") from None
    if sha256(data).hexdigest() != expected:
        raise SetupError("The validation CLI failed integrity verification.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with TemporaryDirectory(prefix=".cli-", dir=destination.parent) as directory:
        staging = Path(directory) / name
        staging.write_bytes(data)
        if platform.system() != "Windows":
            staging.chmod(0o700)
        staging.replace(destination)
    return destination


def init_run(run):
    run = Path(run)
    run.mkdir(parents=True, exist_ok=False)
    qa = {"site": "JP", "status": "skipped", "reason_code": "rufus_us_only",
          "qa_pairs": [], "requested_keywords": [], "question_reviews": []}
    (run / "06_qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "initialized", "site": "JP", "run_dir": str(run.resolve()),
            "qa_status": "skipped", "backend_validated": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a fresh JP run and its explicit QA skip record")
    init.add_argument("--run-dir", type=Path, required=True)
    commands.add_parser("config-status", help="Check shared credentials without displaying them")
    commands.add_parser("configure", help="Merge credential JSON from standard input")
    commands.add_parser("prepare-cli", help="Download and verify the pinned validation CLI")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = init_run(args.run_dir)
        elif args.command == "config-status":
            result = config_status(STATE)
        elif args.command == "configure":
            result = configure(STATE, json.load(sys.stdin))
        else:
            result = {"status": "prepared", "cli_path": str(prepare_backend_cli()),
                      "backend_validated": False}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except SetupError as error:
        print(json.dumps({"status": "failed", "reason": str(error)}))
    except (OSError, ValueError, KeyError, TypeError):
        print('{"status":"failed","reason":"Check the run directory, configuration, and input JSON."}')
    return 1


if __name__ == "__main__":
    sys.exit(main())
