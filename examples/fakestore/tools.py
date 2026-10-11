"""Local tools of the FakeStore shopping agent: what the REST API can't do in one request.

Single-request lookups (categories, products in a category, one product) are HTTP tools in agent_config.json.
Each tool returns (text for the model, data for the UI); the UI renders the data by the tool's name.
"""

import time
from typing import Literal

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

API_URL = "https://fakestoreapi.com"
_cache = {"time": 0.0, "products": []}


async def _get(path: str):
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{API_URL}{path}")
        response.raise_for_status()
        return response.json()


async def all_products() -> list[dict]:
    """All products (the store has 20), cached for 10 minutes. FakeStore has no search endpoint."""
    if time.monotonic() - _cache["time"] > 600:
        _cache.update(products=await _get("/products"), time=time.monotonic())
    return _cache["products"]


def _card(product: dict) -> dict:
    """The fields a product card shows (the long description only appears in details and comparisons)."""
    return {key: product[key] for key in ("id", "title", "price", "category", "image", "rating")}


def _line(product: dict) -> str:
    return (f"#{product['id']} {product['title'].strip()}: ${product['price']}, {product['category']}, "
            f"rated {product['rating']['rate']}/5 ({product['rating']['count']} reviews)")


@tool(response_format="content_and_artifact")
async def search_products(query: str = "", max_price: float | None = None, min_rating: float | None = None,
                          sort: Literal["relevance", "price_low", "price_high", "rating"] = "relevance"):
    """Search products by keywords in the title, description or category, e.g. "backpack", "gold ring", "ssd".

    Optional filters: max_price in US dollars, min_rating from 0 to 5. Sort by relevance, price or rating.
    Use an empty query to browse all products with the filters.
    """
    # Simple matching: lower case, and "jackets" also matches "jacket"
    words = [word.rstrip("s") if len(word) > 3 else word for word in query.lower().split()]

    def score(product):
        text = f"{product['title']} {product['description']} {product['category']}".lower()
        return sum(word in text for word in words)

    products = [p for p in await all_products()
                if (not words or score(p))
                and (max_price is None or p["price"] <= max_price)
                and (min_rating is None or p["rating"]["rate"] >= min_rating)]
    sort_key = {"relevance": lambda p: -score(p), "price_low": lambda p: p["price"],
                "price_high": lambda p: -p["price"], "rating": lambda p: -p["rating"]["rate"]}[sort]
    products = sorted(products, key=sort_key)[:8]

    if not products:
        return f"No products match '{query}' with these filters.", []
    return f"Found {len(products)} products:\n" + "\n".join(map(_line, products)), [_card(p) for p in products]


@tool(response_format="content_and_artifact")
async def compare_products(product_ids: list[int]):
    """Compare two or more products side by side (price, rating, category, description) by their ids."""
    by_id = {p["id"]: p for p in await all_products()}
    products = [by_id[i] for i in product_ids if i in by_id]
    missing = [i for i in product_ids if i not in by_id]
    if len(products) < 2:
        return f"Need at least two existing products to compare (unknown ids: {missing}).", []
    text = "\n".join(f"{_line(p)}. {p['description'][:200]}" for p in products)
    if missing:
        text += f"\nUnknown product ids: {missing}"
    return text, products


@tool(response_format="content_and_artifact")
async def get_my_cart(config: RunnableConfig):
    """Show the signed-in user's shopping cart with quantities and the total."""
    # The user comes from the payload the server passes to stream_chat(), never from the model
    user_id = config["configurable"]["payload"]["user_id"]
    carts = await _get(f"/carts/user/{user_id}")
    if not carts:
        return "Your cart is empty.", None
    cart = max(carts, key=lambda c: c["date"])  # the latest cart
    by_id = {p["id"]: p for p in await all_products()}
    items = [{"product": _card(by_id[item["productId"]]), "quantity": item["quantity"],
              "subtotal": round(by_id[item["productId"]]["price"] * item["quantity"], 2)}
             for item in cart["products"] if item["productId"] in by_id]
    total = round(sum(item["subtotal"] for item in items), 2)
    text = "\n".join(f"{item['quantity']} x {_line(item['product'])}" for item in items)
    return (f"Cart #{cart['id']} with {len(items)} products, total ${total}:\n{text}",
            {"cart_id": cart["id"], "date": cart["date"][:10], "items": items, "total": total})
