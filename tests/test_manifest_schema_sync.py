import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))

from _lib import manifest_path_field, manifest_sync_gaps


class ManifestSchemaSyncTests(unittest.TestCase):
    def setUp(self):
        self.manifest_path = str(REPO_ROOT / "doc" / "harness" / "manifest.yaml")
        self.template_path = str(
            REPO_ROOT / "plugin" / "skills" / "setup" / "templates" / "doc" / "harness" / "manifest.yaml"
        )

    def test_repo_manifest_matches_template_schema(self):
        gaps = manifest_sync_gaps(
            manifest_path=self.manifest_path,
            template_path=self.template_path,
        )
        self.assertEqual(gaps, [])

    def test_nested_manifest_paths_are_readable(self):
        self.assertEqual(
            manifest_path_field("project_meta.shape", manifest_path=self.manifest_path),
            "library",
        )
        self.assertEqual(
            manifest_path_field("browser.enabled", manifest_path=self.manifest_path),
            "false",
        )
        self.assertEqual(
            manifest_path_field("tooling.chrome_devtools_ready", manifest_path=self.manifest_path),
            "false",
        )
        self.assertEqual(
            manifest_path_field(
                "teams.safe_only.require_disjoint_files",
                manifest_path=self.manifest_path,
            ),
            "true",
        )


if __name__ == "__main__":
    unittest.main()
