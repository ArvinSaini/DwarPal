import pytest

from dwarpal.db import dumps, loads, now_utc_day_bounds, tx


def test_schema_creates_tables(conn):
    names = {r["name"] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {
        "products", "enrichments", "agents", "mandates", "policy", "sessions",
        "reservations", "payments", "ledger", "webhook_events",
    } <= names


def test_tx_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with tx(conn):
            conn.execute("insert into webhook_events(event_id, received_at) values ('e1', 1)")
            raise RuntimeError("boom")
    assert conn.execute("select count(*) from webhook_events").fetchone()[0] == 0


def test_tx_commits_and_nests(conn):
    with tx(conn):
        conn.execute("insert into webhook_events(event_id, received_at) values ('e1', 1)")
        with tx(conn):  # nested: joins the outer transaction
            conn.execute("insert into webhook_events(event_id, received_at) values ('e2', 2)")
    assert conn.execute("select count(*) from webhook_events").fetchone()[0] == 2
    assert not conn.in_transaction


def test_utc_day_bounds():
    start, end = now_utc_day_bounds(1_756_900_000)  # 2026-09-03 12:26:40 UTC
    assert start == 1_756_857_600  # 2026-09-03 00:00:00 UTC
    assert end == start + 86_400


def test_json_roundtrip():
    assert loads(dumps({"b": 1, "a": [1, 2]})) == {"a": [1, 2], "b": 1}
    assert loads(None) is None


def test_init_db_adds_the_public_key_column_to_an_older_agents_table():
    from dwarpal.db import connect, init_db
    c = connect(":memory:")
    c.execute("create table agents (id text primary key, name text not null, api_key_hash text not null unique, "
              "status text not null, created_at integer not null)")  # the 0.1.0 shape
    c.execute("insert into agents values ('agt_old', 'old', 'h', 'active', 1)")
    init_db(c)
    cols = {r["name"] for r in c.execute("pragma table_info(agents)")}
    assert "public_key" in cols
    assert c.execute("select public_key from agents where id = 'agt_old'").fetchone()[0] is None
    init_db(c)  # idempotent
    assert c.execute("select count(*) from agent_nonces").fetchone()[0] == 0
