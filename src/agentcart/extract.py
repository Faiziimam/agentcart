"""Extract structured product data (schema.org JSON-LD, OpenGraph, microdata-lite) from HTML."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any

from .sanitize import clean_text, parse_money

MAX_JSONLD_BLOCKS = 20
MAX_JSONLD_BYTES = 512 * 1024


@dataclass
class Product:
    url: str
    name: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    availability: str | None = None
    brand: str | None = None
    seller: str | None = None
    isbn: str | None = None
    sku: str | None = None
    image: str | None = None
    description: str | None = None
    sources: list[str] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        d = asdict(self)
        d["price"] = str(self.price) if self.price is not None else None
        return d


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.meta: dict[str, str] = {}
        self.itemprops: dict[str, str] = {}
        self.title: str | None = None
        self._in_jsonld = False
        self._in_title = False
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").lower().strip() == "application/ld+json":
            self._in_jsonld, self._buf = True, []
        elif tag == "title":
            self._in_title, self._buf = True, []
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        if "itemprop" in a and tag != "meta":
            val = a.get("content") or a.get("href") or a.get("src")
            if val and a["itemprop"].lower() not in self.itemprops:
                self.itemprops[a["itemprop"].lower()] = val

    def handle_data(self, data: str) -> None:
        if self._in_jsonld or self._in_title:
            self._buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            if len(self.jsonld) < MAX_JSONLD_BLOCKS:
                self.jsonld.append("".join(self._buf)[:MAX_JSONLD_BYTES])
        elif tag == "title" and self._in_title:
            self._in_title = False
            self.title = "".join(self._buf)


def _walk(node: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Find every dict whose @type includes Product (handles @graph, lists, nesting)."""
    if depth > 8:
        return []
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node[:200]:
            found += _walk(item, depth + 1)
    elif isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.lower() in ("product", "book", "productgroup") for x in types):
            found.append(node)
        for key in ("@graph", "mainEntity", "itemListElement", "item", "workExample"):
            if key in node:
                found += _walk(node[key], depth + 1)
    return found


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _name_of(value: Any) -> Any:
    value = _first(value)
    return value.get("name") if isinstance(value, dict) else value


def _offer_fields(offers: Any) -> tuple[Decimal | None, str | None, str | None, str | None]:
    offers = _first(offers)
    if not isinstance(offers, dict):
        return None, None, None, None
    if str(offers.get("@type", "")).lower() == "aggregateoffer":
        price = parse_money(offers.get("lowPrice") or offers.get("price"))
    else:
        price = parse_money(offers.get("price"))
        spec = _first(offers.get("priceSpecification"))
        if price is None and isinstance(spec, dict):
            price = parse_money(spec.get("price"))
    currency = clean_text(offers.get("priceCurrency"), 8)
    avail = clean_text(str(offers.get("availability", "")).rsplit("/", 1)[-1], 40)
    seller = clean_text(_name_of(offers.get("seller")), 120)
    return price, currency, avail, seller


def extract_product(html: str, url: str) -> Product:
    c = _Collector()
    try:
        c.feed(html)
        c.close()
    except Exception:  # noqa: S110 - malformed HTML is expected; parse what we can
        pass
    p = Product(url=url)

    for raw in c.jsonld:
        try:
            data = json.loads(raw, parse_float=Decimal)
        except (ValueError, RecursionError):
            continue
        for node in _walk(data):
            p.name = p.name or clean_text(node.get("name"), 200)
            p.brand = p.brand or clean_text(_name_of(node.get("brand") or node.get("author")), 120)
            p.isbn = p.isbn or clean_text(node.get("isbn") or node.get("gtin13"), 20)
            p.sku = p.sku or clean_text(node.get("sku"), 60)
            p.image = p.image or clean_text(_first(node.get("image")) if not isinstance(
                _first(node.get("image")), dict) else _first(node.get("image")).get("url"), 500)
            p.description = p.description or clean_text(node.get("description"), 400)
            price, cur, avail, seller = _offer_fields(node.get("offers"))
            if p.price is None and price is not None:
                p.price, p.currency = price, cur
            p.availability = p.availability or avail
            p.seller = p.seller or seller
            if "json-ld" not in p.sources:
                p.sources.append("json-ld")

    m, ip = c.meta, c.itemprops
    before = (p.name, p.price)
    p.name = p.name or clean_text(m.get("og:title") or ip.get("name") or c.title, 200)
    if p.price is None:
        p.price = parse_money(m.get("product:price:amount") or m.get("og:price:amount") or ip.get("price"))
        p.currency = p.currency or clean_text(
            m.get("product:price:currency") or m.get("og:price:currency") or ip.get("pricecurrency"), 8)
    p.image = p.image or clean_text(m.get("og:image"), 500)
    p.description = p.description or clean_text(m.get("og:description") or m.get("description"), 400)
    p.availability = p.availability or clean_text(
        (m.get("product:availability") or ip.get("availability") or "").rsplit("/", 1)[-1], 40)
    if (p.name, p.price) != before:
        p.sources.append("meta")
    if p.currency:
        p.currency = p.currency.upper()
    return p
