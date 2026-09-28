from __future__ import annotations

import runpy
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = runpy.run_path(str(ROOT / "scripts" / "nas_storage_mapping.py"))


class NasStorageMappingTests(unittest.TestCase):
    def test_uses_longest_matching_mount_and_keeps_source_evidence(self) -> None:
        mount_for = SCRIPT["mount_for"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "docker" / "postgres-data"
            nested.mkdir(parents=True)
            mounts = (
                f"1 0 8:1 / {root.as_posix()} rw - ext4 /dev/sda1 rw\n"
                f"2 1 249:4 / {nested.as_posix()} rw - btrfs /dev/mapper/cachedev_0 rw\n"
            )
            value = mount_for(nested, mounts)
            self.assertEqual("249:4", value["major_minor"])
            self.assertEqual("/dev/mapper/cachedev_0", value["source"])

    def test_unknown_block_device_is_explicitly_unavailable(self) -> None:
        block_graph = SCRIPT["block_graph"]
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual({"major_minor": "249:4", "unavailable": True},
                             block_graph("249:4", sys_dev_block=Path(directory)))


if __name__ == "__main__":
    unittest.main()
