import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/amazon-jp-listing-asin/scripts"
SPEC = importlib.util.spec_from_file_location("skill_config_under_test", SCRIPTS / "skill_config.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "test-only-aba-credential-never-use-in-production"


class SkillConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.root = self.home / ".amazon-jp-listing-asin"

    def configure(self, payload, **kwargs):
        return MODULE.configure(self.root, payload, **kwargs)

    def read(self):
        return json.loads((self.root / "config.json").read_text())

    def test_merging_both_credentials_preserves_existing_fields_and_rotations(self):
        self.configure({"backend_token": "old-backend"})
        data = self.read()
        data["custom"] = {"keep": True}
        (self.root / "config.json").write_text(json.dumps(data))
        self.configure({"aba_mcp": {"access_token": TOKEN}})
        self.configure({"backend_token": "new-backend"})
        self.assertEqual(self.read()["aba_mcp"]["access_token"], TOKEN)
        self.assertEqual(self.read()["custom"], {"keep": True})
        self.configure({"aba_mcp": {"access_token": "rotated-aba-token"}})
        self.assertEqual(self.read()["backend_token"], "new-backend")
        self.assertEqual(MODULE.aba_headers(self.root), {"Authorization": "Bearer rotated-aba-token"})

    def test_creating_aba_config_imports_the_legacy_backend_credential_pair(self):
        legacy = self.home / "legacy.json"
        legacy.write_text(json.dumps({"backend_token": "legacy-backend",
                                      "backend_url": "https://example.invalid/backend", "other": 1}))
        self.configure({"aba_mcp": {"access_token": TOKEN}}, legacy_paths=[legacy])
        self.assertEqual(self.read()["backend_token"], "legacy-backend")
        self.assertEqual(self.read()["backend_url"], "https://example.invalid/backend")
        self.assertEqual(self.read()["other"], 1)

    def test_legacy_import_does_not_change_an_explicit_primary_backend_url(self):
        self.root.mkdir()
        (self.root / "config.json").write_text(json.dumps({"backend_url": "https://primary.invalid"}))
        legacy = self.home / "legacy.json"
        legacy.write_text(json.dumps({"backend_token": "legacy-backend", "backend_url": "https://legacy.invalid"}))
        self.configure({"aba_mcp": {"access_token": TOKEN}}, legacy_paths=[legacy])
        self.assertEqual(self.read()["backend_url"], "https://primary.invalid")
        self.assertEqual(self.read()["backend_token"], "")

    def test_invalid_updates_leave_the_existing_config_unchanged(self):
        self.configure({"backend_token": "keep-backend"})
        previous = (self.root / "config.json").read_bytes()
        for payload in [{}, {"unknown": TOKEN}, {"backend_token": "bad\nvalue"},
                        {"aba_mcp": {"access_token": "Bearer " + TOKEN}},
                        {"aba_mcp": {"access_token": TOKEN, "url": "https://other.invalid"}},
                        {"aba_mcp": {"url": MODULE.ABA_URL}}]:
            with self.assertRaises(ValueError):
                self.configure(payload)
            self.assertEqual((self.root / "config.json").read_bytes(), previous)

    def test_permissions_and_atomic_replacement(self):
        self.configure({"aba_mcp": {"access_token": TOKEN}})
        if os.name == "posix":
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.root / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual([path.name for path in self.root.iterdir()], ["config.json"])

    def test_symlinks_are_refused_without_modifying_the_target(self):
        if os.name != "posix":
            self.skipTest("symlink privilege differs on Windows")
        self.root.mkdir()
        target = self.home / "target.json"
        target.write_text('{"keep":true}')
        (self.root / "config.json").symlink_to(target)
        with self.assertRaises(ValueError):
            self.configure({"backend_token": "new"})
        self.assertEqual(target.read_text(), '{"keep":true}')

    def test_status_is_redacted_and_header_helper_uses_the_single_config(self):
        self.configure({"aba_mcp": {"access_token": TOKEN}})
        environment = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}
        status = subprocess.run([sys.executable, str(SCRIPTS / "environment.py"), "config-status"],
                                env=environment, capture_output=True, text=True, check=True)
        self.assertNotIn(TOKEN, status.stdout + status.stderr)
        self.assertTrue(json.loads(status.stdout)["aba_mcp_access_token_configured"])
        helper = subprocess.run([sys.executable, str(SCRIPTS / "aba_mcp_headers.py")],
                                env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(helper.stdout), {"Authorization": "Bearer " + TOKEN})
        self.assertEqual(helper.stderr, "")
        self.configure({"aba_mcp": {"access_token": "new-aba-token"}})
        helper = subprocess.run([sys.executable, str(SCRIPTS / "aba_mcp_headers.py")],
                                env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(helper.stdout)["Authorization"], "Bearer new-aba-token")

    def test_configuration_stdin_and_errors_never_echo_credentials(self):
        environment = {**os.environ, "HOME": str(self.home), "USERPROFILE": str(self.home)}
        result = subprocess.run([sys.executable, str(SCRIPTS / "environment.py"), "configure"],
                                input=json.dumps({"aba_mcp": {"access_token": TOKEN}}),
                                env=environment, capture_output=True, text=True, check=True)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertEqual(self.read()["aba_mcp"]["access_token"], TOKEN)
        result = subprocess.run([sys.executable, str(SCRIPTS / "environment.py"), "configure"],
                                input=TOKEN, env=environment, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        (self.root / "config.json").write_text(TOKEN)
        helper = subprocess.run([sys.executable, str(SCRIPTS / "aba_mcp_headers.py")],
                                env=environment, capture_output=True, text=True)
        self.assertNotEqual(helper.returncode, 0)
        self.assertEqual(helper.stdout, "")
        self.assertNotIn(TOKEN, helper.stderr)

    def test_screenshot_spapi_field_is_usable_without_reentering_credentials(self):
        self.root.mkdir()
        (self.root / "config.json").write_text(json.dumps({
            "backend_token": "keep-backend", "spapi_mcp": {"access_token": TOKEN, "custom": 1}}))
        self.assertEqual(MODULE.aba_headers(self.root), {"Authorization": "Bearer " + TOKEN})
        status = MODULE.config_status(self.root)
        self.assertTrue(status["aba_mcp_access_token_configured"])
        self.assertEqual(status["aba_mcp_config_source"], "spapi_mcp")
        self.assertNotIn(TOKEN, json.dumps(status))
        MODULE.normalize_config(self.root)
        data = self.read()
        self.assertNotIn("spapi_mcp", data)
        self.assertEqual(data["aba_mcp"]["access_token"], TOKEN)
        self.assertEqual(data["aba_mcp"]["custom"], 1)
        self.assertEqual(data["backend_token"], "keep-backend")

    def test_alias_input_normalizes_and_conflicting_saved_tokens_are_explicit(self):
        self.configure({"spapi_mcp": {"access_token": TOKEN}})
        self.assertEqual(self.read()["aba_mcp"]["access_token"], TOKEN)
        self.assertNotIn("spapi_mcp", self.read())
        data = self.read()
        data["spapi_mcp"] = {"access_token": "different-valid-token"}
        (self.root / "config.json").write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "ABA_MCP_CONFIG_CONFLICT"):
            MODULE.aba_headers(self.root)
        self.assertEqual(MODULE.config_status(self.root)["aba_mcp_config_error"], "ABA_MCP_CONFIG_CONFLICT")
        self.configure({"aba_mcp": {"access_token": "explicit-replacement"}})
        self.assertNotIn("spapi_mcp", self.read())
        self.assertEqual(MODULE.aba_headers(self.root)["Authorization"], "Bearer explicit-replacement")


if __name__ == "__main__":
    unittest.main()
