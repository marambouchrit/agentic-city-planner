"""verify_place: does a place proposed by the LLM really exist? Wikipedia decides, not the LLM."""

import re
import unicodedata
from datetime import date, timedelta
from urllib.parse import quote

import httpx

from city_planner.http import new_client
from city_planner.schemas import Place, VerifyResult

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user"
SEARCH_RESULTS = 5

# Small words that never identify a place.
STOP_WORDS = {"a", "an", "the", "of", "in", "at", "and", "de", "du", "des", "la", "le", "les", "el", "al", "d", "l", "do", "da"}
# Words for the type of place. "Bardo Museum" and "Bardo National Museum" share the name "bardo".
TYPE_WORDS = {
    "museum", "museums", "musee", "museu", "museo", "national", "gallery", "palace", "castle", "tower", "church",
    "cathedral", "mosque", "temple", "monastery", "park", "garden", "gardens", "square", "place", "market", "old",
}

def _words(text: str) -> list[str]:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", plain)


def _contains_city(text: str, city: str) -> bool:
    return f" {' '.join(_words(city))} " in f" {' '.join(_words(text))} "


def _distinctive(text: str, city: str) -> set[str]:
    return set(_words(text)) - STOP_WORDS - TYPE_WORDS - set(_words(city))


def _title_matches(name: str, title: str, city: str) -> bool:
    """The whole name must match. A real word in a fake name, like "Bardo Spoon Museum", is not enough."""
    bracket = re.search(r"\((.*)\)", title)
    # "Louvre (film)" is a different thing, but "Grand Mosque (Tunis)" is fine.
    if bracket and not _contains_city(bracket.group(1), city):
        return False
    base_title = re.sub(r"\(.*\)", "", title)
    if _words(name) == _words(base_title):
        return True
    name_words = _distinctive(name, city)
    return bool(name_words) and name_words == _distinctive(base_title, city)


def _page_text(page: dict) -> str:
    return f"{page.get('title', '')} {page.get('description', '')} {page.get('extract', '')}"


def _find_page(pages: list[dict], name: str, city: str) -> dict | None:
    """Prefers a title with every word of the name, so "Belem Tower" finds the tower, not the district."""
    name_words = set(_words(name)) - STOP_WORDS
    type_words = name_words & TYPE_WORDS
    matches = [
        page
        for page in sorted(pages, key=lambda p: p.get("index", 0))
        if "disambiguation" not in page.get("pageprops", {})
        and _title_matches(name, page["title"], city)
        and _contains_city(_page_text(page), city)
    ]
    for page in matches:
        if name_words <= set(_words(page["title"])):
            return page
    # A looser title must at least describe the same type of place, like "tower" for a tower.
    for page in matches:
        if type_words <= set(_words(_page_text(page))):
            return page
    return None


def _query_pages(client: httpx.Client, params: dict) -> list[dict]:
    response = client.get(
        WIKIPEDIA_API,
        params={
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "coordinates|pageprops|description|extracts|info",
            "inprop": "url",
            "ppprop": "wikibase_item|disambiguation",
            "exintro": 1,
            "explaintext": 1,
            "exlimit": SEARCH_RESULTS,
            "colimit": SEARCH_RESULTS,
            **params,
        },
    )
    response.raise_for_status()
    return response.json().get("query", {}).get("pages", [])


def _lookup_exact_title(client: httpx.Client, name: str, city: str) -> dict | None:
    """Wikipedia's own redirects know other names, like "National Tile Museum"."""
    for page in _query_pages(client, {"titles": name, "redirects": 1}):
        is_real = "missing" not in page and "disambiguation" not in page.get("pageprops", {})
        if is_real and _contains_city(_page_text(page), city):
            return page
    return None


def _wikidata_coordinates(client: httpx.Client, item: str | None) -> tuple[float, float] | None:
    if not item:
        return None
    try:
        response = client.get(
            WIKIDATA_API, params={"action": "wbgetclaims", "entity": item, "property": "P625", "format": "json"}
        )
        response.raise_for_status()
        value = response.json()["claims"]["P625"][0]["mainsnak"]["datavalue"]["value"]
        return value["latitude"], value["longitude"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return None


def _page_views(client: httpx.Client, title: str, today: date) -> int | None:
    """Views in the last full month."""
    month_end = today.replace(day=1) - timedelta(days=1)
    month = month_end.strftime("%Y%m")
    url = f"{PAGEVIEWS_API}/{quote(title.replace(' ', '_'), safe='')}/monthly/{month}0100/{month}{month_end.day}00"
    try:
        response = client.get(url)
        response.raise_for_status()
        return sum(item["views"] for item in response.json()["items"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def verify_place(name: str, city: str, client: httpx.Client | None = None, today: date | None = None) -> VerifyResult:
    """Looks for a Wikipedia article with this name that also mentions the city."""
    if not name.strip() or not city.strip():
        return VerifyResult(status="rejected", reason="A place name and a city are both required.")

    client = client or new_client()
    try:
        pages = _query_pages(client, {"generator": "search", "gsrsearch": f"{name} {city}", "gsrlimit": SEARCH_RESULTS})
        page = _find_page(pages, name, city) or _lookup_exact_title(client, name, city)
    except (httpx.HTTPError, ValueError) as exc:
        return VerifyResult(status="unavailable", reason=f"Wikipedia could not be reached: {type(exc).__name__}")

    if page is None:
        return VerifyResult(status="rejected", reason=f"No Wikipedia article matches '{name}' in {city}.")

    item = page.get("pageprops", {}).get("wikibase_item")
    coordinates = page.get("coordinates") or []
    position = (coordinates[0]["lat"], coordinates[0]["lon"]) if coordinates else _wikidata_coordinates(client, item)
    views = _page_views(client, page["title"], today or date.today())

    missing = []
    if position is None:
        missing.append("coordinates")
    if views is None:
        missing.append("page_views")

    place = Place(
        place_id=f"wikipedia:{page['pageid']}",
        name=page["title"],
        kind="attraction",
        source="wikipedia",
        source_url=page.get("fullurl"),
        latitude=position[0] if position else None,
        longitude=position[1] if position else None,
        description=page.get("description"),
        page_views=views,
        missing=missing,
    )
    return VerifyResult(status="verified", place=place, reason=f"Matched the Wikipedia article '{page['title']}'.")
