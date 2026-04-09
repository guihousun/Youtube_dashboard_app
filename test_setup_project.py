import json
import shutil
import unittest
from pathlib import Path

from setup_project import bootstrap_workspace, render_env_file


class SetupProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_root = Path("test_setup_tmp")
        self.test_root.mkdir(exist_ok=True)

    def tearDown(self) -> None:
        if self.test_root.exists():
            shutil.rmtree(self.test_root, ignore_errors=True)

    def test_render_env_file_outputs_public_api_key_only(self) -> None:
        content = render_env_file(api_key="api-key")
        self.assertIn("YT_API_KEY=api-key", content)
        self.assertNotIn("YT_CLIENT_ID", content)
        self.assertNotIn("YT_CLIENT_SECRET", content)

    def test_bootstrap_workspace_creates_public_project_files(self) -> None:
        result = bootstrap_workspace(
            self.test_root,
            api_key="api-key",
        )

        self.assertTrue((self.test_root / "output" / "playwright").exists())
        self.assertTrue((self.test_root / "snapshots").exists())
        self.assertTrue((self.test_root / ".env").exists())
        self.assertTrue((self.test_root / ".env.example").exists())
        self.assertTrue((self.test_root / "accounts.json").exists())
        self.assertEqual(result["accountsCreated"], True)
        self.assertEqual(result["envCreated"], True)

        accounts = json.loads((self.test_root / "accounts.json").read_text(encoding="utf-8"))
        self.assertEqual(accounts, [])

    def test_bootstrap_workspace_preserves_existing_accounts_and_env(self) -> None:
        (self.test_root / "accounts.json").write_text(
            '[{"id":"custom","handle":"@demo","alias":"员工A","label":"员工A"}]',
            encoding="utf-8",
        )
        (self.test_root / ".env").write_text("YT_API_KEY=old\n", encoding="utf-8")

        result = bootstrap_workspace(
            self.test_root,
            api_key="new",
            overwrite_env=False,
        )

        self.assertFalse(result["accountsCreated"])
        self.assertFalse(result["envCreated"])
        self.assertIn("YT_API_KEY=old", (self.test_root / ".env").read_text(encoding="utf-8"))
        accounts = json.loads((self.test_root / "accounts.json").read_text(encoding="utf-8"))
        self.assertEqual(accounts[0]["id"], "custom")


if __name__ == "__main__":
    unittest.main()
