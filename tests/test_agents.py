import pytest

from dwarpal.agents import AgentStore, hash_key


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


# -- request signing: an agent may register an Ed25519 public key -----------------------------------

def test_register_with_a_public_key_stores_it_and_marks_the_agent_as_signing(conn, clock):
    from dwarpal.signing import generate_keypair
    store = AgentStore(conn, clock)
    _, public_b64 = generate_keypair()
    agent, key = store.register("signer", public_key=public_b64)
    assert agent.public_key == public_b64 and agent.signs_requests
    assert store.authenticate(key).public_key == public_b64
    plain, _ = store.register("plain")
    assert plain.public_key is None and not plain.signs_requests
    assert plain.to_dict()["signs_requests"] is False and agent.to_dict()["signs_requests"] is True


def test_register_rejects_a_malformed_public_key(conn, clock):
    store = AgentStore(conn, clock)
    with pytest.raises(ValueError):
        store.register("signer", public_key="not-a-key")
    assert store.all() == []


def test_nonces_are_remembered_per_agent_and_pruned(conn, clock):
    store = AgentStore(conn, clock)
    a, _ = store.register("a")
    b, _ = store.register("b")
    assert store.remember_nonce(a.id, "n1", clock.now) is True
    assert store.remember_nonce(a.id, "n1", clock.now) is False   # replay
    assert store.remember_nonce(b.id, "n1", clock.now) is True    # another agent's nonce space
    clock.tick(10_000)
    assert store.prune_nonces(clock.now, max_age_s=600) == 2
    assert store.remember_nonce(a.id, "n1", clock.now) is True    # forgotten after pruning
