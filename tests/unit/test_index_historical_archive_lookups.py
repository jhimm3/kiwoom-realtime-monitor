import sqlite3
import unittest

from scripts.index_historical_archive_lookups import INDEXES, QUERIES, _install_indices, _plans


class ArchiveLookupIndexTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE central_news_article_revisions(accepted_sequence INTEGER PRIMARY KEY,
                article_revision_id TEXT UNIQUE,stock_code TEXT,identity TEXT,
                available_at REAL,document_json TEXT);
            CREATE INDEX idx_article ON central_news_article_revisions(stock_code,identity,available_at DESC,accepted_sequence DESC);
            CREATE TABLE central_news_body_revisions(accepted_sequence INTEGER PRIMARY KEY,
                body_revision_id TEXT UNIQUE,article_revision_id TEXT,available_at REAL);
            CREATE TABLE central_news_event_revisions(accepted_sequence INTEGER PRIMARY KEY,
                event_revision_id TEXT UNIQUE,event_id TEXT,event_key TEXT,stock_code TEXT,
                article_revision_id TEXT,body_revision_id TEXT,certainty TEXT,available_at REAL);
            CREATE INDEX idx_event ON central_news_event_revisions(event_id,available_at DESC,accepted_sequence DESC);
            CREATE TABLE central_news_event_membership_revisions(accepted_sequence INTEGER PRIMARY KEY,
                membership_revision_id TEXT UNIQUE,event_id TEXT,event_revision_id TEXT,
                article_revision_id TEXT,body_revision_id TEXT,available_at REAL);
            CREATE INDEX idx_member ON central_news_event_membership_revisions(event_id,available_at DESC,accepted_sequence DESC);
        ''')
        for n in range(2500):
            # Publication/availability order deliberately differs from sequence.
            identity = 'index-probe' if n % 100 == 0 else f'url-{n%200}'
            event = 'index-probe' if n % 100 == 0 else f'group-{n%200}'
            self.db.execute('INSERT INTO central_news_article_revisions VALUES(?,?,?,?,?,?)',
                            (n+1, f'a{n}', '005930', identity, 2500-n, f'doc-{n}'))
            self.db.execute('INSERT INTO central_news_body_revisions VALUES(?,?,?,?)',
                            (n+1, f'b{n}', f'a{n}', 2500-n))
            self.db.execute('INSERT INTO central_news_event_revisions VALUES(?,?,?,?,?,?,?,?,?)',
                            (n+1, f'e{n}', event, identity, '005930', f'a{n}', f'b{n}', 'confirmed', 2500-n))
            self.db.execute('INSERT INTO central_news_event_membership_revisions VALUES(?,?,?,?,?,?,?)',
                            (n+1, f'm{n}', event, f'e{n}', f'a{n}', f'b{n}', 2500-n))
        self.db.commit()

    def test_all_query_results_and_rows_preserved_and_plans_use_keys(self):
        before = {k: sorted(self.db.execute(sql, args).fetchall()) for k, (sql, args) in QUERIES.items()}
        tables = tuple(dict.fromkeys(x[1] for x in INDEXES))
        contents = {t: list(self.db.execute('SELECT * FROM '+t+' ORDER BY accepted_sequence')) for t in tables}
        self.db.execute('BEGIN IMMEDIATE')
        self.assertEqual(len(INDEXES), len(_install_indices(self.db)))
        self.db.commit()
        after = {k: sorted(self.db.execute(sql, args).fetchall()) for k, (sql, args) in QUERIES.items()}
        self.assertEqual(before, after)
        self.assertEqual(contents, {t: list(self.db.execute('SELECT * FROM '+t+' ORDER BY accepted_sequence')) for t in tables})
        self.assertEqual([('a2400',)], self.db.execute(*QUERIES['previous_article']).fetchall())
        plans = _plans(self.db)
        for name in ('event_identity', 'event_key', 'previous_membership'):
            self.assertFalse(any('SCAN ' in row[3] for row in plans[name]), plans[name])
        for name in ('recent100', 'previous_article', 'previous_event', 'previous_membership'):
            self.assertFalse(any('FOR ORDER BY' in row[3] for row in plans[name]), plans[name])

    def test_sql_failure_rolls_back_entire_index_set(self):
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaises(sqlite3.OperationalError):
            _install_indices(self.db, INDEXES+(('archive_perf_bad', 'central_news_article_revisions', 'missing_column'),))
        self.db.rollback()
        self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'archive_perf_%'").fetchone())
        self.assertEqual(2500, self.db.execute('SELECT COUNT(*) FROM central_news_article_revisions').fetchone()[0])

    def test_replay_is_idempotent_and_conflicting_definition_rejected(self):
        self.db.execute('BEGIN IMMEDIATE'); _install_indices(self.db); self.db.commit()
        self.db.execute('BEGIN IMMEDIATE')
        self.assertEqual([], _install_indices(self.db)); self.db.commit()
        self.db.execute('DROP INDEX archive_perf_article_identity')
        self.db.execute('CREATE INDEX archive_perf_article_identity ON central_news_article_revisions(stock_code)')
        self.db.execute('BEGIN IMMEDIATE')
        with self.assertRaisesRegex(ValueError, 'definition differs'):
            _install_indices(self.db)
        self.db.rollback()

    def test_caller_transaction_required(self):
        with self.assertRaisesRegex(ValueError, 'transaction required'):
            _install_indices(self.db)


if __name__ == '__main__':
    unittest.main()
