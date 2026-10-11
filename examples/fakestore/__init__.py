"""The FakeStore shopping agent shared by the custom UI and AG-UI examples (data from https://fakestoreapi.com)."""

import os

from bootstrap_agent import ChatAgent

from .tools import compare_products, get_my_cart, search_products

# The signed-in user (FakeStore has users 1-10, each with carts). A real application takes this from its own
# session or auth on the server, never from the browser.
DEMO_USER_ID = int(os.getenv("STORE_USER_ID", "1"))


async def create_store_agent(name: str = "fakestore-agent") -> ChatAgent:
    config_path = os.path.join(os.path.dirname(__file__), "agent_config.json")
    return await ChatAgent.create(name, config_path, tools=[search_products, compare_products, get_my_cart])
