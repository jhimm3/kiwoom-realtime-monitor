from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.update_planner import build_update_plan


def release(version: str, update_size: int, setup_size: int = 0) -> dict[str, object]:
    assets: list[dict[str, object]] = [
        {
            "name": f"KiwoomMonitor-Update-{version}.zip",
            "browser_download_url": f"https://example.test/{version}.zip",
            "size": update_size,
        },
        {
            "name": f"KiwoomMonitor-Update-{version}.zip.sha256",
            "browser_download_url": f"https://example.test/{version}.zip.sha256",
            "size": 98,
        },
    ]
    if setup_size:
        assets.append(
            {
                "name": f"KiwoomMonitor-Setup-{version}.exe",
                "browser_download_url": f"https://example.test/{version}.exe",
                "size": setup_size,
            }
        )
    return {"tag_name": f"v{version}", "html_url": f"https://example.test/{version}", "assets": assets}


class UpdatePlannerTest(unittest.TestCase):
    def test_uses_all_intermediate_releases_in_version_order(self) -> None:
        plan = build_update_plan(
            "1.1.1",
            [release("1.1.4", 40, 300), release("1.1.2", 30), release("1.1.3", 20)],
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(("1.1.2", "1.1.3", "1.1.4"), tuple(step.version for step in plan.steps))
        self.assertEqual(90, plan.update_size)
        self.assertFalse(plan.should_use_setup)

    def test_recommends_setup_when_updates_are_larger(self) -> None:
        plan = build_update_plan(
            "1.1.1",
            [release("1.1.4", 200, 250), release("1.1.2", 100), release("1.1.3", 100)],
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertTrue(plan.should_use_setup)

    def test_requires_every_step_to_have_checksum(self) -> None:
        broken = release("1.1.2", 10, 100)
        broken["assets"] = [asset for asset in broken["assets"] if not str(asset["name"]).endswith(".sha256")]
        plan = build_update_plan("1.1.1", [broken])
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertFalse(plan.can_apply_steps)
