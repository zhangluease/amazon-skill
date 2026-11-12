import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/amazon-jp-keywords/scripts"
SPEC = importlib.util.spec_from_file_location("shared_config_under_test", ROOT / "shared/amazon_config.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "synthetic-aba-credential-never-use-in-production"
BACKEND_TOKEN = "synthetic-backend-credential-never-use-in-production"


class SkillConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / ".amazon-skill"
        self.environment = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}

    def configure(self, payload):
        return MODULE.configure(self.root, payload)

    def read(self):
        return json.loads((self.root / "config.json").read_text())

    def run_script(self, script, *args, data=None):
        return subprocess.run([sys.executable, str(script), *args], input=data, env=self.environment,
                              capture_output=True, text=True)

    def test_initialize_creates_blank_config_and_never_overwrites_configured_values(self):
        self.assertEqual(MODULE.init_config(self.root)["status"], "initialized")
        self.assertEqual(self.read(), MODULE.DEFAULT_CONFIG)
        self.configure({"backend_token": BACKEND_TOKEN})
        before = (self.root / "config.json").read_bytes()
        self.assertEqual(MODULE.init_config(self.root)["status"], "exists")
        self.assertEqual((self.root / "config.json").read_bytes(), before)

    def test_merging_and_rotating_services_preserves_the_other_service_and_custom_fields(self):
        self.configure({"backend_token": BACKEND_TOKEN})
        data = self.read()
        data["custom"] = {"keep": True}
        (self.root / "config.json").write_text(json.dumps(data))
        self.configure({"aba_mcp": {"access_token": TOKEN}})
        self.configure({"backend_token": "rotated-backend"})
        self.assertEqual(self.read()["aba_mcp"]["access_token"], TOKEN)
        self.assertEqual(self.read()["custom"], {"keep": True})
        self.configure({"aba_mcp": {"access_token": "rotated-aba"}})
        self.assertEqual(self.read()["backend_token"], "rotated-backend")
        self.assertEqual(MODULE.aba_headers(self.root), {"Authorization": "Bearer rotated-aba"})

    def test_invalid_updates_leave_the_previous_config_unchanged(self):
        self.configure({"backend_token": BACKEND_TOKEN})
        before = (self.root / "config.json").read_bytes()
        for payload in [{}, {"unknown": TOKEN}, {"backend_token": "bad\nvalue"},
                        {"backend_token": None}, {"backend_url": "https://user:password@example.invalid"},
                        {"aba_mcp": {"access_token": "Bearer " + TOKEN}},
                        {"aba_mcp": {"access_token": TOKEN, "url": "https://other.invalid"}},
                        {"aba_mcp": {"url": MODULE.ABA_URL}}, {"spapi_mcp": {"access_token": TOKEN}}]:
            with self.subTest(payload_keys=list(payload)):
                with self.assertRaises(ValueError):
                    self.configure(payload)
                self.assertEqual((self.root / "config.json").read_bytes(), before)

    def test_changing_backend_url_requires_its_own_explicit_token(self):
        self.configure({"backend_token": BACKEND_TOKEN})
        with self.assertRaisesRegex(ValueError, "BACKEND_URL_CHANGE_REQUIRES_TOKEN"):
            self.configure({"backend_url": "https://another.invalid"})
        self.configure({"backend_url": "https://another.invalid", "backend_token": "different-service-token"})
        self.assertEqual(self.read()["backend_url"], "https://another.invalid")
        self.assertEqual(self.read()["backend_token"], "different-service-token")

    def test_clearing_one_credential_keeps_the_other_channel_usable(self):
        self.configure({"backend_token": BACKEND_TOKEN, "aba_mcp": {"access_token": TOKEN}})
        self.configure({"backend_token": ""})
        self.assertFalse(MODULE.config_status(self.root)["backend_token_configured"])
        self.assertEqual(MODULE.aba_headers(self.root), {"Authorization": "Bearer " + TOKEN})
        self.configure({"aba_mcp": {"access_token": ""}})
        with self.assertRaisesRegex(ValueError, "ABA_MCP_ACCESS_TOKEN_MISSING"):
            MODULE.aba_headers(self.root)

    def test_permissions_atomic_replacement_and_no_sidecar_files(self):
        self.configure({"aba_mcp": {"access_token": TOKEN}})
        if os.name == "posix":
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.root / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.root.iterdir()], ["config.json"])

    def test_symlinks_cannot_redirect_shared_credential_updates(self):
        if os.name != "posix":
            self.skipTest("symlink privilege differs on Windows")
        self.root.mkdir()
        target = self.home / "target.json"
        target.write_text('{"keep":true}')
        (self.root / "config.json").symlink_to(target)
        with self.assertRaises(ValueError):
            self.configure({"backend_token": BACKEND_TOKEN})
        self.assertEqual(target.read_text(), '{"keep":true}')

    def test_one_project_configuration_is_visible_to_both_separately_installed_skills(self):
        result = self.run_script(ROOT / "scripts/configure.py", "configure", data=json.dumps({
            "backend_token": BACKEND_TOKEN, "aba_mcp": {"access_token": TOKEN}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertNotIn(BACKEND_TOKEN, result.stdout + result.stderr)
        for skill, entry in [("amazon-jp-keywords", "environment.py"), ("amazon-jp-listing", "listing_environment.py")]:
            installed = self.home / "separate-projects" / skill / ".trae/skills" / skill / "scripts"
            shutil.copytree(ROOT / "skills" / skill / "scripts", installed, ignore=shutil.ignore_patterns("__pycache__"))
            result = self.run_script(installed / entry, "config-status")
            self.assertEqual(result.returncode, 0, result.stderr)
            status = json.loads(result.stdout)
            self.assertEqual(status["config_file"], str(self.root / "config.json"))
            self.assertTrue(status["backend_token_configured"])
            self.assertTrue(status["aba_mcp_access_token_configured"])
            self.assertNotIn(TOKEN, result.stdout + result.stderr)
            if skill == "amazon-jp-listing":
                code = ("import sys,json;sys.path.insert(0,sys.argv[1]);"
                        "from listing_environment import config_path;from backend_cli import load_config;"
                        "print(json.dumps({'configured':bool(load_config(config_path())['backend_token'])}))")
                result = self.run_script("-c", code, str(installed))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["configured"])

    def test_each_skill_configuration_entry_updates_the_same_public_file(self):
        result = self.run_script(SCRIPTS / "environment.py", "configure", data=json.dumps({"backend_token": BACKEND_TOKEN}))
        self.assertEqual(result.returncode, 0, result.stderr)
        listing_scripts = ROOT / "skills/amazon-jp-listing/scripts"
        result = self.run_script(listing_scripts / "listing_environment.py", "configure", data=json.dumps({"aba_mcp": {"access_token": TOKEN}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read()["backend_token"], BACKEND_TOKEN)
        result = self.run_script(SCRIPTS / "aba_mcp_headers.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"Authorization": "Bearer " + TOKEN})

    def test_old_skill_configurations_are_not_searched_or_imported(self):
        previous = self.home / ".amazon-jp-listing-asin"
        previous.mkdir()
        (previous / "config.json").write_text(json.dumps({"backend_token": BACKEND_TOKEN,
                                                         "aba_mcp": {"access_token": TOKEN}}))
        result = self.run_script(SCRIPTS / "environment.py", "config-status")
        status = json.loads(result.stdout)
        self.assertFalse(status["exists"])
        self.assertFalse(status["backend_token_configured"])
        result = self.run_script(ROOT / "scripts/configure.py", "init")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read(), MODULE.DEFAULT_CONFIG)

    def test_configuration_errors_and_status_never_echo_credentials(self):
        result = self.run_script(ROOT / "scripts/configure.py", "configure", data=TOKEN)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.configure({"backend_token": BACKEND_TOKEN})
        data = self.read()
        data["aba_mcp"] = {"access_token": "bad\n" + TOKEN}
        (self.root / "config.json").write_text(json.dumps(data))
        status = MODULE.config_status(self.root)
        self.assertTrue(status["backend_token_configured"])
        self.assertEqual(status["aba_mcp_config_error"], "INVALID_TOKEN")
        self.assertNotIn(TOKEN, json.dumps(status))
        (self.root / "config.json").write_text(TOKEN)
        result = self.run_script(SCRIPTS / "aba_mcp_headers.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(TOKEN, result.stderr)

    def test_shared_reader_bundles_and_public_template_match_the_common_source(self):
        source = (ROOT / "shared/amazon_config.py").read_bytes()
        for skill in ("amazon-jp-keywords", "amazon-jp-listing"):
            self.assertEqual((ROOT / "skills" / skill / "scripts/amazon_config.py").read_bytes(), source)
        self.assertEqual(json.loads((ROOT / "config.example.json").read_text()), MODULE.DEFAULT_CONFIG)


if __name__ == "__main__":
    unittest.main()
