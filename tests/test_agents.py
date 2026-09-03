import pytest

from agentgate.agents import AgentStore, hash_key


def test_register_and_authenticate(conn, clock):
    store = AgentStore(conn, clock)
    agent, key = store.register("shopbot")
    assert key.startswith("agk_") and len(key) > 20
    assert agent.id.startswith("agt_") and agent.status == "active" and agent.name == "shopbot"
    assert store.authenticate(key).id == agent.id


def test_unknown_key_is_none(conn, clock):
    store = AgentStore(conn, clock)
    assert store.authenticate("agk_wrong") is None
    assert store.authenticate("") is None
    assert store.get("agt_nope") is None


def test_key_is_hashed_at_rest(conn, clock):
    store = AgentStore(conn, clock)
    agent, key = store.register("shopbot")
    stored = conn.execute("select api_key_hash from agents where id = ?", (agent.id,)).fetchone()[0]
    assert stored == hash_key(key) and stored != key


def test_revoke_keeps_authentication_but_changes_status(conn, clock):
    store = AgentStore(conn, clock)
    agent, key = store.register("shopbot")
    revoked = store.revoke(agent.id)
    assert revoked.status == "revoked"
    assert store.authenticate(key).status == "revoked"
    assert store.get(agent.id).status == "revoked"


def test_all_lists_in_creation_order(conn, clock):
    store = AgentStore(conn, clock)
    a, _ = store.register("a")
    clock.tick()
    b, _ = store.register("b")
    assert [x.id for x in store.all()] == [a.id, b.id]


def test_revoke_unknown_raises(conn, clock):
    with pytest.raises(KeyError):
        AgentStore(conn, clock).revoke("agt_nope")
