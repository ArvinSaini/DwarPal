from agentgate.catalog import SEED_PRODUCTS
from agentgate.crosssell import FakePicker, NoPicker, Offer, candidates, headroom
from agentgate.mandates import Mandate
from agentgate.policy import DEFAULT_POLICY

NOW = 1_756_900_000
BY_ID = {p.id: p for p in SEED_PRODUCTS}


def mandate(**over) -> Mandate:
    base = dict(id="mnd_1", agent_id="agt_1", currency="INR", per_txn_cap_paise=400000, daily_cap_paise=800000,
                total_cap_paise=2000000, categories=[], starts_at=NOW, expires_at=NOW + 86400, status="active",
                created_at=NOW)
    base.update(over)
    return Mandate(**base)


def test_headroom_is_min_of_gaps():
    assert headroom(mandate(), DEFAULT_POLICY, 249900, 0, 0) == 150100
    assert headroom(mandate(), DEFAULT_POLICY, 249900, 700000, 0) == 0
    assert headroom(mandate(total_cap_paise=300000), DEFAULT_POLICY, 249900, 0, 0) == 50100
    assert headroom(mandate(), dict(DEFAULT_POLICY, max_order_paise=300000), 249900, 0, 0) == 50100


def test_candidates_filter_and_sort():
    cart = [BY_ID["prod_shoes"]]
    got = candidates(cart, SEED_PRODUCTS, DEFAULT_POLICY, mandate(), 0, 0)
    ids = [p.id for p in got]
    assert "prod_shoes" not in ids and "prod_brace" not in ids and "prod_watch" not in ids
    assert ids == ["prod_socks", "prod_cap", "prod_bottle", "prod_tee", "prod_bands", "prod_mat", "prod_gel"]


def test_candidates_respect_mandate_categories_and_limit():
    cart = [BY_ID["prod_shoes"]]
    got = candidates(cart, SEED_PRODUCTS, DEFAULT_POLICY, mandate(categories=["footwear", "apparel"]), 0, 0)
    assert [p.id for p in got] == ["prod_socks", "prod_tee"]
    got = candidates(cart, SEED_PRODUCTS, DEFAULT_POLICY, mandate(), 0, 0, limit=3)
    assert [p.id for p in got] == ["prod_socks", "prod_cap", "prod_bottle"]


def test_candidates_respect_headroom():
    cart = [BY_ID["prod_shoes"]]
    got = candidates(cart, SEED_PRODUCTS, DEFAULT_POLICY, mandate(per_txn_cap_paise=300000), 0, 0)
    assert all(p.price_paise <= 50100 for p in got) and [p.id for p in got] == ["prod_socks"]
    assert candidates(cart, SEED_PRODUCTS, DEFAULT_POLICY, mandate(per_txn_cap_paise=249900), 0, 0) == []


def test_fake_picker_prefers_tag_overlap_then_price():
    cart = [BY_ID["prod_shoes"]]
    cands = candidates(cart, SEED_PRODUCTS, DEFAULT_POLICY, mandate(), 0, 0)
    offers = FakePicker().pick(cart, cands)
    assert [o.id for o in offers] == ["prod_socks", "prod_cap"]
    assert offers[0].price_paise == 49900 and offers[0].title and offers[0].reason
    assert offers[0].to_dict() == {"id": "prod_socks", "title": offers[0].title, "price_paise": 49900,
                                   "reason": offers[0].reason}


def test_no_candidates_no_offers():
    assert FakePicker().pick([BY_ID["prod_shoes"]], []) == []
    assert NoPicker().pick([BY_ID["prod_shoes"]], [BY_ID["prod_socks"]]) == []
    assert isinstance(Offer("x", "t", 1, "r"), Offer)


# -- LLM picker -----------------------------------------------------------------------------------

def test_llm_picker_validates_ids_and_caps_two():
    from agentgate.crosssell import LLMPicker
    from agentgate.llm import FakeLLM

    cart = [BY_ID["prod_shoes"]]
    cands = candidates(cart, SEED_PRODUCTS, DEFAULT_POLICY, mandate(), 0, 0)
    llm = FakeLLM(['[{"id":"prod_socks","reason":"pairs with shoes"},{"id":"prod_watch","reason":"x"},'
                   '{"id":"prod_socks","reason":"dup"},{"id":"prod_cap","reason":"sun"},{"id":"prod_bottle","reason":"y"}]'])
    offers = LLMPicker(llm, "m").pick(cart, cands)
    assert [o.id for o in offers] == ["prod_socks", "prod_cap"] and offers[0].reason == "pairs with shoes"
    assert "prod_watch" not in llm.calls[0]["messages"][1]["content"]  # never offered to the model


def test_llm_picker_bad_output_or_no_candidates_gives_no_offers():
    from agentgate.crosssell import LLMPicker
    from agentgate.llm import FakeLLM

    cart = [BY_ID["prod_shoes"]]
    cands = candidates(cart, SEED_PRODUCTS, DEFAULT_POLICY, mandate(), 0, 0)
    assert LLMPicker(FakeLLM(["nope"]), "m").pick(cart, cands) == []
    assert LLMPicker(FakeLLM(['{"id":"prod_socks"}']), "m").pick(cart, cands) == []
    llm = FakeLLM([])
    assert LLMPicker(llm, "m").pick(cart, []) == [] and llm.calls == []
