from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import publish_historical_daishin_raw_to_nas as publisher  # noqa: E402


class PublishHistoricalDaishinRawTests(unittest.TestCase):
    def test_publishes_verified_raw_run_and_points_to_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "historical_collection"
            first = source / "daishin" / "005930" / "one.ndjson"
            retry = source / "daishin-retry" / "005930" / "retry.ndjson"
            for path, body in ((first, b"first\n"), (retry, b"retry\n")):
                path.parent.mkdir(parents=True)
                path.write_bytes(body)
            nas = root / "nas"
            server_data = nas / "deploy" / "synology" / "server-data"
            main_root = server_data / "historical-intelligence" / "v1"
            main_root.mkdir(parents=True)
            (nas / "AGENTS.md").write_text("test", encoding="utf-8")
            (main_root / "latest.json").write_text(
                '{"run":"main-test-run"}', encoding="utf-8"
            )

            result = publisher.publish(source, nas)
            published_root = server_data / "historical-intelligence" / "daishin-raw-v1"
            latest = json.loads((published_root / "latest.json").read_text(encoding="utf-8"))
            manifest = json.loads((published_root / "runs" / latest["run"] / "manifest.json")
                                  .read_text(encoding="utf-8"))
            self.assertEqual(result["files"], 2)
            self.assertEqual(latest["database_run"], "main-test-run")
            self.assertEqual(manifest["database_run"], "main-test-run")
            self.assertEqual(manifest["bytes"], len(b"first\n") + len(b"retry\n"))
            for entry in manifest["artifacts"]:
                published = published_root / "runs" / latest["run"] / entry["name"]
                self.assertEqual(entry["sha256"], hashlib.sha256(published.read_bytes()).hexdigest())
            self.assertFalse(list((published_root / "runs").glob(".staging-*")))

    def test_refuses_directory_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "historical_collection"
            for folder in publisher.RAW_FOLDERS:
                (source / folder).mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            try:
                (source / "daishin" / "link").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks are unavailable")
            with self.assertRaisesRegex(ValueError, "directory link"):
                publisher.raw_files(source)


if __name__ == "__main__":
    unittest.main()
