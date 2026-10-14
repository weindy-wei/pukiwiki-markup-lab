"""Step32 native PukiWiki integration static/fail-closed installer acceptance."""
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/"tools/wiki_auth"
spec=importlib.util.spec_from_file_location("filmwiki_step32_install",BASE/"install_step32.py")
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class NativeWikiBridgeInstallerTests(unittest.TestCase):
    def auth_template(self):
        return (
            "<?php\n"
            "define('AUTH_TYPE_FORM', 3);\n"
            "function is_page_readable($page) {\n\tglobal $read_auth_pages, $read_auth;\n}\n"
            "function is_page_writable($page) {\n\tglobal $edit_auth_pages, $edit_auth;\n}\n"
            "function _is_page_accessible($page, $auth_pages) {\n\tglobal $auth_method_type, $auth_user_groups, $auth_user;\n}\n"
            "function edit_auth($page, $auth_enabled = TRUE, $exit_on_fail = TRUE)\n{\n"
            "\tglobal $edit_auth, $edit_auth_pages, $_title_cannotedit;\n}\n"
            "function read_auth($page, $auth_enabled = TRUE, $exit_on_fail = TRUE)\n{\n"
            "\tglobal $read_auth, $read_auth_pages, $_title_cannotread;\n}\n")
    def html_template(self):
        return (
            "<?php\nfunction edit_form($page) {\n"
            "\t$s_original  = isset($vars['original']) ? htmlsc($vars['original']) : $s_postdata;\n"
            "$body=<<<EOD\n"
            "  <input type=\"hidden\" name=\"cmd\"    value=\"edit\" />\n"
            "</form><form>\n"
            "  <input type=\"hidden\" name=\"cmd\"    value=\"edit\" />\n"
            "EOD;\n}\n")
    def test_protected_namespace_only_and_native_edit_csrf(self):
        changed=installer.plan_auth(self.auth_template())
        self.assertIn("FilmWiki Step32 managed namespace integration",changed)
        self.assertIn("fw-demo-editor-read",changed)
        self.assertIn("fw-demo-editor-edit",changed)
        self.assertIn("filmwiki_unified_refresh($page)",changed)
        self.assertEqual(changed.count("filmwiki_unified_edit_csrf_ok()"),2)
        self.assertNotIn("$read_auth = 1",changed)
        self.assertNotIn("$edit_auth = 1",changed)
        self.assertEqual(installer.plan_auth(changed),changed)
    def test_native_edit_form_has_csrf_hidden_field(self):
        changed=installer.plan_edit_html(self.html_template())
        self.assertIn("filmwiki_unified_session_start()",changed)
        self.assertIn('name="filmwiki_csrf"',changed)
        self.assertIn('  $filmwiki_unified_csrf_input\n',changed)
        self.assertEqual(installer.plan_edit_html(changed),changed)
    def test_refuse_divergent_pukiwiki_core(self):
        with self.assertRaises(ValueError):installer.plan_auth("PukiWiki unknown")
        with self.assertRaises(ValueError):installer.plan_edit_html("PukiWiki unknown")
    def test_refuse_legacy_unprotected_step32_hook(self):
        old=self.auth_template()+"\n// FilmWiki Step32 managed namespace integration\n"
        with self.assertRaises(ValueError):installer.plan_auth(old)
    def test_real_php_bridge_guards(self):
        php=(BASE/"filmwiki_unified_bridge.php").read_text()
        login=(BASE/"fwunified.inc.php").read_text()
        self.assertIn("SQLITE3_OPEN_READONLY",php)
        self.assertIn("hash_pbkdf2('sha256'",php)
        self.assertIn("600000,32,true",php)
        self.assertIn("session.cookie_httponly",php)
        self.assertIn("session.cookie_samesite",php)
        self.assertIn("hash_equals($actor['session_guard']",php)
        self.assertIn("filmwiki_unified_edit_csrf_ok",php)
        self.assertIn("filmwiki_unified_guard",login)
        self.assertNotIn("HTTP_X_FORWARDED_USER",php)

if __name__=="__main__":unittest.main()
