import json
from decimal import Decimal

from agentcart.extract import extract_product
from agentcart.sanitize import clean_text, parse_money


def page(ld, extra=""):
    return f"<html><head><script type='application/ld+json'>{json.dumps(ld)}</script>{extra}</head></html>"


def test_book_jsonld():
    p = extract_product(page({"@type": "Book", "name": "Atomic Habits", "isbn": "9781847941831",
                              "author": {"name": "James Clear"},
                              "offers": {"price": "399", "priceCurrency": "inr",
                                         "availability": "https://schema.org/InStock"}}), "u")
    assert (p.name, p.price, p.currency, p.availability, p.brand) == (
        "Atomic Habits", Decimal("399.00"), "INR", "InStock", "James Clear")


def test_graph_and_aggregate_offer():
    ld = {"@graph": [{"@type": "WebPage"}, {"@type": ["Product"], "name": "X",
                                             "offers": {"@type": "AggregateOffer", "lowPrice": 1299.5,
                                                        "priceCurrency": "INR"}}]}
    p = extract_product(page(ld), "u")
    assert p.price == Decimal("1299.50")


def test_meta_fallback():
    html = ("<html><head><meta property='og:title' content='Gita'>"
            "<meta property='product:price:amount' content='₹1,250.00'>"
            "<meta property='product:price:currency' content='INR'></head></html>")
    p = extract_product(html, "u")
    assert (p.name, p.price, p.currency) == ("Gita", Decimal("1250.00"), "INR")


def test_malformed_jsonld_ignored():
    html = "<script type='application/ld+json'>{not json</script><title>T</title>"
    assert extract_product(html, "u").name == "T"


def test_invisible_chars_stripped_and_truncated():
    assert clean_text("buy‮now​  please") == "buynow please"
    assert len(clean_text("x" * 1000, 50)) == 50


def test_money_bounds():
    assert parse_money("-5") is None
    assert parse_money("99999999999") is None
    assert parse_money(True) is None
