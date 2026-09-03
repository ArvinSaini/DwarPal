import pytest

from dwarpal.catalog import Catalog, seed
from dwarpal.enrichment import EnrichmentStore, FakeEnricher, LLMEnricher, Proposal, validate_proposal
from dwarpal.ledger import Ledger
from dwarpal.llm import FakeLLM

ALLOWED = ["footwear", "apparel", "accessories", "fitness"]


@pytest.fixture
def store(conn, clock):
    catalog = Catalog(conn, clock)
    seed(catalog, raw=True)
    ledger = Ledger(conn, clock)
    return EnrichmentStore(conn, catalog, ledger, clock), catalog, ledger


def test_validate_proposal_accepts_and_normalises():
    p = validate_proposal({"category": " Footwear ", "attributes": {"use": "trail"},
                           "tags": ["Running", "running", " Trail "], "recommend_when": "x"}, ALLOWED)
    assert isinstance(p, Proposal) and p.category == "footwear" and p.tags == ["running", "trail"]
    assert validate_proposal({"category": "other"}, ALLOWED).category == "other"


@pytest.mark.parametrize("raw", [
    {"category": "weapons"},
    {"category": "footwear", "attributes": {f"k{i}": "v" for i in range(9)}},
    {"category": "footwear", "recommend_when": "x" * 201},
    {"category": "footwear", "tags": "running"},
    {"category": "footwear", "attributes": {"k": 1}},
    "not a dict",
    [],
])
def test_validate_proposal_rejects(raw):
    with pytest.raises(ValueError):
        validate_proposal(raw, ALLOWED)


def test_validate_proposal_caps_tags_at_eight():
    p = validate_proposal({"category": "fitness", "tags": [f"t{i}" for i in range(12)]}, ALLOWED)
    assert len(p.tags) == 8


def test_fake_enricher_categorises_seed_catalog(store):
    _, catalog, _ = store
    expected = {"prod_shoes": "footwear", "prod_socks": "apparel", "prod_bottle": "accessories", "prod_mat": "fitness",
                "prod_bands": "fitness", "prod_tee": "apparel", "prod_cap": "accessories", "prod_watch": "other",
                "prod_brace": "fitness", "prod_gel": "fitness"}
    for pid, cat in expected.items():
        prop = FakeEnricher().propose(catalog.get(pid), ALLOWED)
        assert prop is not None and prop.category == cat, pid
        assert prop.tags and prop.recommend_when


def test_propose_all_only_uncategorised_and_logs(store):
    enrichments, catalog, ledger = store
    catalog.set_enrichment("prod_shoes", "footwear", {}, ["running"], "x")
    created = enrichments.propose_all(FakeEnricher(), ALLOWED)
    assert len(created) == 9 and all(e.status == "pending" and e.model == "fake-rules" for e in created)
    assert len(enrichments.pending()) == 9
    assert [e.type for e in ledger.events()].count("catalog.enrichment.proposed") == 9
    assert enrichments.propose_all(FakeEnricher(), ALLOWED) == []  # pending proposals are not duplicated
    assert len(enrichments.propose_all(FakeEnricher(), ALLOWED, only_uncategorised=False)) == 1  # the shoes


def test_approve_updates_product_and_logs(store):
    enrichments, catalog, ledger = store
    created = enrichments.propose_all(FakeEnricher(), ALLOWED)
    socks = next(e for e in created if e.product_id == "prod_socks")
    done = enrichments.approve(socks.id)
    assert done.status == "approved" and done.decided_at is not None
    p = catalog.get("prod_socks")
    assert p.category == "apparel" and p.tags and p.recommend_when
    assert ledger.events()[-1].type == "catalog.enrichment.approved"
    assert ledger.events()[-1].payload["product_id"] == "prod_socks"
    with pytest.raises(ValueError):
        enrichments.approve(socks.id)
    with pytest.raises(KeyError):
        enrichments.approve("enr_nope")
    assert len(created) == 10
    assert len(enrichments.pending()) == 9 and len(enrichments.all()) == 10


def test_reject_keeps_product_untouched(store):
    enrichments, catalog, ledger = store
    created = enrichments.propose_all(FakeEnricher(), ALLOWED)
    watch = next(e for e in created if e.product_id == "prod_watch")
    assert enrichments.reject(watch.id).status == "rejected"
    assert catalog.get("prod_watch").category is None
    assert ledger.events()[-1].type == "catalog.enrichment.rejected"
    # a rejected product can be proposed again
    assert [e.product_id for e in enrichments.propose_all(FakeEnricher(), ALLOWED)] == ["prod_watch"]


def test_llm_enricher_valid_invalid_and_untrusted_prompt(store):
    _, catalog, _ = store
    gel = catalog.get("prod_gel")
    llm = FakeLLM(['{"category":"Fitness","attributes":{"count":"12"},"tags":["Running","gel"],"recommend_when":"long runs"}'])
    prop = LLMEnricher(llm, "test-model").propose(gel, ALLOWED)
    assert prop.category == "fitness" and prop.tags == ["running", "gel"] and prop.attributes == {"count": "12"}
    user_msg = llm.calls[0]["messages"][1]["content"]
    assert "<untrusted_product_text>" in user_msg and "SYSTEM NOTE TO AI AGENTS" in user_msg
    assert '"fitness"' in llm.calls[0]["messages"][0]["content"]
    assert LLMEnricher(FakeLLM(["not json"]), "m").propose(gel, ALLOWED) is None
    assert LLMEnricher(FakeLLM(['{"category":"weapons"}']), "m").propose(gel, ALLOWED) is None
    assert LLMEnricher(FakeLLM([]), "m").propose(gel, ALLOWED) is None
    assert LLMEnricher(llm, "test-model").name == "test-model"


def test_skipped_proposals_are_recorded(store):
    enrichments, catalog, ledger = store
    created = enrichments.propose_all(LLMEnricher(FakeLLM(["garbage"] * 10), "m"), ALLOWED)
    assert created == []
    assert [e.type for e in ledger.events()].count("catalog.enrichment.skipped") == 10
