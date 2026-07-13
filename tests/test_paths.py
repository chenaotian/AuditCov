from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auditcov_mcp.paths import (
    DB_FILENAME,
    WorkDirError,
    change_work_dir,
    config_path,
    default_db_path,
    default_work_dir,
    workdir_settings,
)


class PathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "install"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_default_db_path_uses_install_work_dir(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(default_db_path(self.root), (default_work_dir(self.root) / DB_FILENAME).resolve())

    def test_change_work_dir_moves_existing_content(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            source = default_work_dir(self.root)
            source.mkdir()
            (source / DB_FILENAME).write_text("db", encoding="utf-8")
            target = self.root / "state"

            result = change_work_dir(str(target), self.root)

            self.assertTrue(result["moved"])
            self.assertFalse(source.exists())
            self.assertEqual((target / DB_FILENAME).read_text(encoding="utf-8"), "db")
            self.assertEqual(json.loads(config_path(self.root).read_text())["work_dir"], str(target.resolve()))
            self.assertEqual(default_db_path(self.root), (target / DB_FILENAME).resolve())

    def test_change_work_dir_rejects_non_empty_target(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            source = default_work_dir(self.root)
            source.mkdir()
            target = self.root / "state"
            target.mkdir()
            (target / "existing.txt").write_text("keep", encoding="utf-8")

            with self.assertRaises(WorkDirError):
                change_work_dir(str(target), self.root)

    def test_settings_are_locked_by_auditcov_db_env(self) -> None:
        with patch.dict("os.environ", {"AUDITCOV_DB": str(self.root / "custom.sqlite3")}, clear=True):
            settings = workdir_settings(self.root)

            self.assertFalse(settings["can_update_work_dir"])
            self.assertEqual(settings["override_reason"], "AUDITCOV_DB is set")
            with self.assertRaises(WorkDirError):
                change_work_dir(str(self.root / "state"), self.root)


if __name__ == "__main__":
    unittest.main()
