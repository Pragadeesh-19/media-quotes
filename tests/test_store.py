import sqlite3

from mqc.store import Store

OLD_QUOTES_TABLE = """
CREATE TABLE quotes (
    quote_id TEXT PRIMARY KEY, article_id TEXT NOT NULL, text TEXT NOT NULL, start_sid TEXT NOT NULL,
    end_sid TEXT NOT NULL, kind TEXT NOT NULL, speaker TEXT, section TEXT NOT NULL, topic_key TEXT NOT NULL,
    claim_type TEXT NOT NULL, extracted_at TEXT NOT NULL, run_id TEXT NOT NULL
);
INSERT INTO quotes VALUES ('q1', 'a1', 'Kyiv was hit.', 'b0.s0', 'b0.s0', 'authorial', NULL,
    'War Intensity & Battlefield', 't', 'event', '2026-11-18', 'r1');
"""


def test_existing_database_gains_prompt_version_with_v1_for_old_quotes(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_QUOTES_TABLE)
    conn.close()

    store = Store(path)

    row = store.conn.execute("SELECT prompt_version, text FROM quotes WHERE quote_id = 'q1'").fetchone()
    assert tuple(row) == ("v1", "Kyiv was hit.")
    store.close()


def test_opening_a_migrated_database_again_is_harmless(tmp_path):
    path = tmp_path / "db.db"
    Store(path).close()
    Store(path).close()
