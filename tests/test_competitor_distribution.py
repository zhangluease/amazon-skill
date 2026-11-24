import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/amazon-jp-competitor-tracking'
SPEC = importlib.util.spec_from_file_location('package_competitor_skill', ROOT / 'scripts/package_competitor_skill.py')
pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pack)


class DistributionTests(unittest.TestCase):
    def test_archive_excludes_sessions_profiles_reports_dependencies_and_extra_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'source'
            shutil.copytree(SKILL, source, ignore=shutil.ignore_patterns('node_modules', '__pycache__'))
            for relative in ('http-session.json', 'scripts/http-session.json', 'scripts/debug.py',
                             'profile/state.json', 'reports/products.json', 'node_modules/private.js'):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('private-fixture-must-not-ship')
            output = Path(tmp) / 'bundle.zip'
            manifest = pack.package_skill(source, output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {'amazon-jp-competitor-tracking/' + p for p in pack.FILES})
                for name in archive.namelist():
                    self.assertNotIn(b'private-fixture-must-not-ship', archive.read(name))
            self.assertEqual(len(manifest['files']), len(pack.FILES))
            self.assertTrue(output.with_suffix('.manifest.json').is_file())

    def test_relocated_bundle_runs_without_repository_config_or_browser_cache(self):
        with tempfile.TemporaryDirectory(prefix='competitor clean room ') as tmp:
            root = Path(tmp) / '独立 使用'
            root.mkdir()
            output = root / 'bundle.zip'
            pack.package_skill(SKILL, output)
            with zipfile.ZipFile(output) as archive:
                archive.extractall(root)
            scripts = root / 'amazon-jp-competitor-tracking/scripts'
            home = root / 'new-user'
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), LOCALAPPDATA=str(home / 'local'),
                       XDG_CONFIG_HOME=str(home / 'config'), XDG_DATA_HOME=str(home / 'data'),
                       XDG_CACHE_HOME=str(home / 'cache'), PLAYWRIGHT_BROWSERS_PATH=str(home / 'browsers'),
                       AMAZON_COMPETITOR_STATE_DIR=str(home / 'state'),
                       POD_IMAGE_SELECTION_STATE_DIR=str(home / 'pod-state'),
                       AMAZON_COMPETITOR_PYTHON=sys.executable, PYTHONPATH='', PYTHONNOUSERSITE='1')
            node = shutil.which('node')
            self.assertIsNotNone(node)
            entry = str(scripts / 'competitor.cjs')
            checked = subprocess.run([node, entry, 'check'], env=env, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(checked.returncode, 2, checked.stderr)
            report = json.loads(checked.stdout)
            self.assertEqual(report['status'], 'needs_setup')
            self.assertFalse(report['session']['file_exists'])
            self.assertFalse(report['extension']['found'])
            self.assertFalse(report['runtime']['playwright_installed'])
            self.assertIn('EXTENSION_NOT_FOUND', [issue['code'] for issue in report['issues']])
            self.assertFalse((home / 'state').exists())
            valid = subprocess.run([node, entry, 'validate-input', '--as-of', '2026-11-23', 'B0CDCD694T'],
                                   env=env, cwd=root, capture_output=True, text=True, timeout=15)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertEqual(json.loads(valid.stdout)['asins'], ['B0CDCD694T'])
            help_result = subprocess.run([node, entry, '--help'], env=env, cwd=root,
                                         capture_output=True, text=True, timeout=15)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn('--all-history', help_result.stdout)
            sync_help = subprocess.run([sys.executable, '-S', str(scripts / 'feishu_sync.py'), '--help'],
                                       env=env, cwd=root, capture_output=True, text=True, timeout=15)
            self.assertEqual(sync_help.returncode, 0, sync_help.stderr)
            self.assertIn('--job-id', sync_help.stdout)
            # Synthetic API response exercises the shipped parsing and renderers offline.
            driver = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import competitor_client as c
class Source:
    def get(self, route, params):
        assert route == '/v2/extension/listing/JP/asin-change/B0CDCD694T'
        return [{'timePoint':'2025-08-31 00:00:00','changeType':4,
                 'previousImages':['old'],'currentImages':['new']}]
out = Path(sys.argv[2])
print(json.dumps(c.collect_listing_changes(['B0CDCD694T'], Source(),
      c.history_window('2026-11-23', True), out)))
'''
            run = subprocess.run([sys.executable, '-S', '-c', driver, str(scripts), str(root / 'result')],
                                 env=env, cwd=root, capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            summary = json.loads(run.stdout)
            self.assertEqual(summary['media_changed_products'], 1)
            for name in ('changes.json', 'changes.csv', 'report.html'):
                self.assertTrue((root / 'result' / name).is_file())


if __name__ == '__main__':
    unittest.main()
