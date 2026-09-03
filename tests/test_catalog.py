import pytest

from dwarpal.catalog import SEED_PRODUCTS, Catalog, Product, seed


@pytest.fixture
def catalog(conn, clock):
    return Catalog(conn, clock)


def test_seed_inserts_ten_products(catalog):
    assert seed(catalog) == 10
    assert len(catalog.all()) == 10
    shoes = catalog.get("prod_shoes")
    assert shoes.category == "footwear" and shoes.price_paise == 249900 and shoes.source == "seed"
    assert catalog.get("prod_brace").availability == "out_of_stock"
    assert catalog.get("prod_watch").category == "electronics"


def test_seed_raw_leaves_products_uncategorised(catalog):
    seed(catalog, raw=True)
    assert all(p.category is None and p.tags == [] and p.attributes == {} and p.recommend_when is None
               for p in catalog.all())


def test_upsert_roundtrips_and_replaces(catalog):
    catalog.upsert(Product(id="prod_x", title="X", description="d", price_paise=100,
                           attributes={"size": "M"}, tags=["a"]))
    got = catalog.get("prod_x")
    assert got.attributes == {"size": "M"} and got.tags == ["a"] and got.price_paise == 100
    catalog.upsert(Product(id="prod_x", title="X2", description="d", price_paise=200))
    assert catalog.get("prod_x").title == "X2" and len(catalog.all()) == 1
    assert catalog.get("prod_missing") is None


def test_feed_shows_enrichment_only_once_set(catalog):
    seed(catalog, raw=True)
    row = next(r for r in catalog.feed() if r["id"] == "prod_shoes")
    assert row["category"] is None and row["tags"] == [] and row["recommend_when"] is None
    assert row["title"] and row["price_paise"] == 249900 and row["currency"] == "INR"
    catalog.set_enrichment("prod_shoes", "footwear", {"use": "trail"}, ["running"], "when running")
    row = next(r for r in catalog.feed() if r["id"] == "prod_shoes")
    assert row["category"] == "footwear" and row["tags"] == ["running"] and row["attributes"] == {"use": "trail"}


def test_feed_search_and_category_filter(catalog):
    seed(catalog)
    assert [r["id"] for r in catalog.feed(q="SHOE")] == ["prod_shoes"]
    assert {r["category"] for r in catalog.feed(category="fitness")} == {"fitness"}
    assert catalog.feed(q="zzz") == []


def test_snapshot_shape(catalog):
    seed(catalog)
    snap = catalog.snapshot()
    assert set(snap) == {p.id for p in SEED_PRODUCTS}
    assert set(snap["prod_shoes"]) == {"id", "title", "price_paise", "availability", "category"}


def test_set_availability(catalog):
    seed(catalog)
    catalog.set_availability("prod_shoes", "out_of_stock")
    assert catalog.get("prod_shoes").availability == "out_of_stock"
    with pytest.raises(ValueError):
        catalog.set_availability("prod_shoes", "maybe")
