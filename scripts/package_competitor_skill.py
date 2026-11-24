#!/usr/bin/env python3
"""Build a standalone skill ZIP from an explicit public-file allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'SKILL.md', 'package.json', 'agents/openai.yaml',
    'references/access.md', 'references/data-contract.md', 'references/keepa-trend.md',
    'references/feishu.md',
    'scripts/competitor.cjs', 'scripts/diagnostics.cjs', 'scripts/environment.cjs',
    'scripts/sellersprite_auth.cjs', 'scripts/sellersprite_session.cjs',
    'scripts/competitor_client.py', 'scripts/competitor_core.py', 'scripts/competitor_changes.py',
    'scripts/competitor_report.py', 'scripts/amazon_product.py', 'scripts/amazon_offer_page.js',
    'scripts/amazon_config.py', 'scripts/keepa_probe.py',
    'scripts/feishu_plan.py', 'scripts/feishu_cli.py',
    'scripts/competitor_coordination.py', 'scripts/feishu_sync.py',
)


def package_skill(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.suffix.lower() != '.zip':
        raise ValueError('OUTPUT_ZIP_REQUIRED')
    entries = []
    for relative in FILES:
        path = source / relative
        if path.is_symlink() or not path.is_file() or source not in path.resolve().parents:
            raise ValueError('REQUIRED_PUBLIC_FILE_MISSING_OR_LINKED: ' + relative)
        entries.append((relative, path.read_bytes()))
    metadata = json.loads(dict(entries)['package.json'])
    if metadata.get('name') != 'amazon-jp-competitor-tracking':
        raise ValueError('INVALID_SKILL_PACKAGE')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in entries:
            archive.writestr('amazon-jp-competitor-tracking/' + relative, data)
    manifest = {'skill': metadata['name'], 'version': metadata.get('version'),
                'archive_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                'files': {relative: hashlib.sha256(data).hexdigest() for relative, data in entries}}
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                                   encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'skills/amazon-jp-competitor-tracking')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = package_skill(args.source, args.out)
    except (OSError, ValueError) as error:
        parser.exit(2, str(error) + '\n')
    print(json.dumps({'output': str(args.out.resolve()), 'files': len(manifest['files']),
                      'archive_sha256': manifest['archive_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
