#!/usr/bin/env python3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_from_drive as sync


class FilterTests(unittest.TestCase):
    def test_skips_google_docs_and_sheets(self):
        self.assertFalse(
            sync.is_allowed_file(
                "Dot 1.gdoc", "application/vnd.google-apps.document"
            )
        )
        self.assertFalse(
            sync.is_allowed_file(
                "Budget.gsheet", "application/vnd.google-apps.spreadsheet"
            )
        )
        self.assertFalse(
            sync.is_allowed_file(
                "Talk.gslides", "application/vnd.google-apps.presentation"
            )
        )

    def test_allows_website_files(self):
        self.assertTrue(sync.is_allowed_file("index.html", "text/html"))
        self.assertTrue(sync.is_allowed_file("styles.css", "text/css"))
        self.assertTrue(sync.is_allowed_file("CNAME", "text/plain"))
        self.assertTrue(sync.is_allowed_file("CoverP.jpg", "image/jpeg"))

    def test_skips_drive_desktop_stub_extensions(self):
        self.assertFalse(sync.is_allowed_file("note.gdoc", "application/json"))

    def test_skips_hidden_workflow_dirs(self):
        self.assertTrue(sync.is_skipped_dir(".github"))
        self.assertTrue(sync.is_skipped_dir(".git"))
        self.assertFalse(sync.is_skipped_dir("POSTS"))


class PathTests(unittest.TestCase):
    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(sync.safe_join(root, "../secret.html"))
            self.assertIsNone(sync.safe_join(root, ".github/workflows/x.yml"))
            self.assertIsNotNone(sync.safe_join(root, "POSTS/DOT1.html"))

    def test_write_file_detects_unchanged_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.html"
            path.write_bytes(b"hello")
            self.assertEqual(sync.write_file(path, b"hello", dry_run=False), "unchanged")
            self.assertEqual(sync.write_file(path, b"world", dry_run=True), "would-update")


class SyncTests(unittest.TestCase):
    def test_overlay_writes_allowed_files_and_skips_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "keep-me.html").write_text("stays unless deleted")

            def fake_collect(service, folder_id, prefix=""):
                return {"index.html": "file-1", "css/styles.css": "file-2"}

            def fake_download(service, file_id):
                return b"from-drive" if file_id == "file-1" else b"css-from-drive"

            with patch.object(sync, "resolve_sync_folder_id", return_value="folder"), patch.object(
                sync, "collect_drive_files", fake_collect
            ), patch.object(sync, "download_file", fake_download):
                changes = sync.sync(
                    root, service=None, folder_id="folder", delete_missing=False, dry_run=False
                )

            self.assertEqual(changes, 2)
            self.assertEqual((root / "index.html").read_bytes(), b"from-drive")
            self.assertEqual((root / "css" / "styles.css").read_bytes(), b"css-from-drive")
            self.assertEqual((root / "keep-me.html").read_text(), "stays unless deleted")

    def test_delete_missing_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "old.html").write_text("remove me")

            with patch.object(sync, "resolve_sync_folder_id", return_value="folder"), patch.object(
                sync, "collect_drive_files", return_value={}
            ), patch.object(sync, "download_file"):
                sync.sync(root, None, "folder", delete_missing=True, dry_run=False)

            self.assertFalse((root / "old.html").exists())

    def test_preserves_theme_chrome_from_drive_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            theme = root / "css" / "theme.css"
            scripts = root / "js" / "scripts.js"
            theme.parent.mkdir()
            scripts.parent.mkdir()
            theme.write_text("local-theme")
            scripts.write_text("local-scripts")

            def fake_collect(service, folder_id, prefix=""):
                return {"css/theme.css": "file-theme", "js/scripts.js": "file-js", "index.html": "file-1"}

            def fake_download(service, file_id):
                return b"from-drive"

            with patch.object(sync, "resolve_sync_folder_id", return_value="folder"), patch.object(
                sync, "collect_drive_files", fake_collect
            ), patch.object(sync, "download_file", fake_download):
                sync.sync(root, None, "folder", delete_missing=True, dry_run=False)

            self.assertEqual(theme.read_text(), "local-theme")
            self.assertEqual(scripts.read_text(), "local-scripts")
            self.assertEqual((root / "index.html").read_bytes(), b"from-drive")


class ResolveFolderTests(unittest.TestCase):
    def test_uses_named_subfolder(self):
        tree = {
            "projects": [
                {"id": "web", "name": "Website (DotsLog)", "mimeType": sync.FOLDER_MIME},
                {"id": "work", "name": "Work", "mimeType": sync.FOLDER_MIME},
            ],
            "web": [{"id": "idx", "name": "index.html", "mimeType": "text/html"}],
            "work": [{"id": "note", "name": "notes.html", "mimeType": "text/html"}],
        }

        def fake_list(service, folder_id):
            return tree[folder_id]

        with patch.object(sync, "list_children", fake_list):
            chosen = sync.resolve_sync_folder_id(None, "projects", subfolder="Website (DotsLog)")
        self.assertEqual(chosen, "web")

    def test_auto_picks_only_child_with_index_html(self):
        tree = {
            "projects": [
                {"id": "web", "name": "Website (DotsLog)", "mimeType": sync.FOLDER_MIME},
                {"id": "ai", "name": "AI sandbox", "mimeType": sync.FOLDER_MIME},
                {"id": "work", "name": "Work", "mimeType": sync.FOLDER_MIME},
            ],
            "web": [{"id": "idx", "name": "index.html", "mimeType": "text/html"}],
            "ai": [{"id": "py", "name": "app.py", "mimeType": "text/x-python"}],
            "work": [],
        }

        def fake_list(service, folder_id):
            return tree[folder_id]

        with patch.object(sync, "list_children", fake_list):
            chosen = sync.resolve_sync_folder_id(None, "projects")
        self.assertEqual(chosen, "web")

    def test_folder_names_ignore_spaces_and_case(self):
        self.assertTrue(sync.folder_names_match("Website (DotsLog)", "website(dotslog)"))
        self.assertFalse(sync.folder_names_match("Website", "Work"))


class MainTests(unittest.TestCase):
    def test_skips_when_secrets_are_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(sync.main(), 0)


if __name__ == "__main__":
    unittest.main()
