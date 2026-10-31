"""Shared HTTP settings for the tools."""

import httpx

# Transitous and Wikimedia both ask for the app name, version and a contact.
USER_AGENT = "agentic-city-planner/0.1 (+https://github.com/marambouchrit/agentic-city-planner)"
TIMEOUT_SECONDS = 30.0


def new_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, follow_redirects=True)
