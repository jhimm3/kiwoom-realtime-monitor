from __future__ import annotations

import unittest

from tests.integration import test_theme_generation_postgres as generation
from tests.unit.test_theme_current_snapshot import ThemeCurrentSnapshotContract


class PostgresThemeCurrentSnapshotTests(ThemeCurrentSnapshotContract, unittest.TestCase):
    """Reuse the dedicated-PC PG schema lifecycle without rerunning unrelated history cases."""

    @classmethod
    def setUpClass(cls):
        generation.PostgresThemeGenerationTests.setUpClass.__func__(cls)

    setUp = generation.PostgresThemeGenerationTests.setUp
    drop_schema = generation.PostgresThemeGenerationTests.drop_schema
    new_store = generation.PostgresThemeGenerationTests.new_store
