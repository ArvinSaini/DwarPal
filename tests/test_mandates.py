import pytest

from dwarpal.agents import AgentStore
from dwarpal.mandates import MandateError, MandateStore


@pytest.fixture
def stores(conn, clock):
    agents = AgentStore(conn, clock)
    agent, _ = agents.register("shopbot")
    return MandateStore(conn, clock), agent


def make(mandates, agent, clock, **over):
    kw = dict(per_txn_cap_paise=400000, daily_cap_paise=800000, total_cap_paise=2000000,
              categories=[], expires_at=clock.now + 86400)
    kw.update(over)
    return mandates.create(agent.id, **kw)


def test_create_and_active_for(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock, categories=["footwear"])
    assert m.id.startswith("mnd_") and m.status == "active" and m.starts_at == clock.now
    assert mandates.active_for(agent.id, clock.now).id == m.id
    assert mandates.get(m.id).categories == ["footwear"]
    assert mandates.get("mnd_nope") is None


def test_second_mandate_revokes_first(stores, clock):
    mandates, agent = stores
    first = make(mandates, agent, clock)
    clock.tick()
    second = make(mandates, agent, clock)
    assert mandates.get(first.id).status == "revoked"
    assert mandates.active_for(agent.id, clock.now).id == second.id
    assert [m.id for m in mandates.for_agent(agent.id)] == [first.id, second.id]


def test_active_for_respects_window(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock, starts_at=clock.now + 10, expires_at=clock.now + 20)
    assert mandates.active_for(agent.id, clock.now) is None
    assert mandates.active_for(agent.id, clock.now + 10).id == m.id
    assert mandates.active_for(agent.id, clock.now + 20) is None


def test_revoke(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock)
    assert mandates.revoke(m.id).status == "revoked"
    assert mandates.active_for(agent.id, clock.now) is None
    with pytest.raises(KeyError):
        mandates.revoke("mnd_nope")


def test_create_validates(stores, clock):
    mandates, agent = stores
    with pytest.raises(MandateError):
        make(mandates, agent, clock, per_txn_cap_paise=-1)
    with pytest.raises(MandateError):
        make(mandates, agent, clock, expires_at=clock.now - 1)
    with pytest.raises(MandateError):
        make(mandates, agent, clock, categories="footwear")


def test_reserve_counts_in_spent(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock)
    r = mandates.reserve("cs_1", m.id, 100000)
    assert r.state == "reserved" and r.id.startswith("rsv_")
    assert mandates.open_for("cs_1").id == r.id
    assert mandates.spent(m.id, clock.now) == (100000, 100000)


def test_commit_keeps_counting_and_release_removes(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock)
    mandates.reserve("cs_1", m.id, 100000)
    mandates.reserve("cs_2", m.id, 50000)
    assert mandates.commit("cs_1").state == "committed"
    assert mandates.release("cs_2").state == "released"
    assert mandates.spent(m.id, clock.now) == (100000, 100000)
    assert mandates.open_for("cs_1") is None and mandates.open_for("cs_2") is None


def test_double_reserve_same_session_raises(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock)
    mandates.reserve("cs_1", m.id, 100000)
    with pytest.raises(MandateError):
        mandates.reserve("cs_1", m.id, 1)


def test_commit_or_release_without_open_reservation_raises(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock)
    mandates.reserve("cs_1", m.id, 100000)
    mandates.release("cs_1")
    with pytest.raises(MandateError):
        mandates.commit("cs_1")
    with pytest.raises(MandateError):
        mandates.release("cs_1")
    with pytest.raises(MandateError):
        mandates.commit("cs_unknown")


def test_spent_can_exclude_a_session(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock)
    mandates.reserve("cs_1", m.id, 100000)
    mandates.reserve("cs_2", m.id, 50000)
    assert mandates.spent(m.id, clock.now, exclude_session="cs_1") == (50000, 50000)


def test_spent_today_excludes_yesterday(stores, clock):
    mandates, agent = stores
    m = make(mandates, agent, clock, expires_at=clock.now + 10 * 86400)
    mandates.reserve("cs_1", m.id, 100000)
    clock.tick(86400)
    mandates.reserve("cs_2", m.id, 25000)
    assert mandates.spent(m.id, clock.now) == (125000, 25000)
