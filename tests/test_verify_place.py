"""Offline tests of verify_place, using fake HTTP responses."""

from datetime import date

import httpx

from city_planner.tools.verify_place import verify_place

TODAY = date(2026, 3, 15)


def page(title, extract="", **extra):
    return {"pageid": 1, "index": 1, "title": title, "extract": extract, "fullurl": "https://w/x", **extra}


def fake_client(pages=None, wiki_status=200, wikidata=None, views_status=200, seen=None, title_pages=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if "en.wikipedia.org" in request.url.host:
            found = title_pages if "titles" in request.url.params else pages
            return httpx.Response(wiki_status, json={"query": {"pages": found or []}})
        if "wikidata" in request.url.host:
            return httpx.Response(200, json=wikidata or {"claims": {}})
        return httpx.Response(views_status, json={"items": [{"views": 100}, {"views": 20}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def verify(name, city, **kwargs):
    return verify_place(name, city, client=fake_client(**kwargs), today=TODAY)


def test_real_place_is_verified_with_coordinates_and_views():
    pages = [page("Louvre", "The Louvre is a museum in Paris.", coordinates=[{"lat": 48.86, "lon": 2.33}])]
    result = verify("Louvre Museum", "Paris", pages=pages)
    assert result.status == "verified"
    assert result.place.name == "Louvre"
    assert (result.place.latitude, result.place.longitude) == (48.86, 2.33)
    assert result.place.page_views == 120
    assert result.place.missing == []


def test_fake_place_is_rejected_when_no_title_matches():
    pages = [page("Bardo National Museum", "A museum in Tunis.")]
    assert verify("Museum of Invisible Spoons", "Tunis", pages=pages).status == "rejected"


def test_fake_name_using_a_real_word_is_rejected():
    pages = [page("Bardo, Tunisia", "A town near Tunis."), page("Bardo", "A town near Tunis.")]
    assert verify("Bardo Spoon Museum", "Tunis", pages=pages).status == "rejected"


def test_city_name_alone_cannot_verify_a_fake_place():
    assert verify("Fake Museum of Tunis", "Tunis", pages=[page("Tunis", "Tunis is a city.")]).status == "rejected"


def test_right_title_in_another_city_is_rejected():
    pages = [page("Museum of Modern Art", "An art museum in New York City.")]
    assert verify("Museum of Modern Art", "Tunis", pages=pages).status == "rejected"


def test_generic_name_alone_is_rejected():
    assert verify("Museum", "Paris", pages=[page("Louvre", "A museum in Paris.")]).status == "rejected"


def test_disambiguation_page_is_skipped():
    pages = [page("Bardo", "Bardo may refer to a place in Tunis.", pageprops={"disambiguation": ""})]
    assert verify("Bardo", "Tunis", pages=pages).status == "rejected"


def test_bracket_title_needs_the_city():
    assert verify("Louvre", "Paris", pages=[page("Louvre (film)", "A film set in Paris.")]).status == "rejected"
    assert verify("Grand Mosque", "Tunis", pages=[page("Grand Mosque (Tunis)", "A mosque.")]).status == "verified"


def test_accents_are_ignored():
    pages = [page("Musée d'Orsay", "A museum in Paris.")]
    assert verify("Musee d'Orsay", "Paris", pages=pages).status == "verified"


def test_full_title_beats_a_district_found_first():
    # Found in a live test: "Belem Tower" used to match the district "Belém, Lisbon".
    pages = [page("Belém, Lisbon", "A district of Lisbon."), page("Belém Tower", "A fort in Lisbon.", index=2)]
    assert verify("Belem Tower", "Lisbon", pages=pages).place.name == "Belém Tower"


def test_district_is_not_accepted_as_a_tower():
    assert verify("Belem Tower", "Lisbon", pages=[page("Belém, Lisbon", "A district of Lisbon.")]).status == "rejected"


def test_looser_title_is_accepted_when_the_text_gives_the_type():
    pages = [page("Louvre", "The Louvre is a national art museum in Paris.")]
    assert verify("Louvre Museum", "Paris", pages=pages).status == "verified"


def test_wikipedia_redirect_finds_another_name():
    target = page("Museu Nacional do Azulejo", "A tile museum in Lisbon.")
    assert verify("National Tile Museum", "Lisbon", title_pages=[target]).place.name == "Museu Nacional do Azulejo"


def test_exact_title_in_another_city_is_rejected():
    assert verify("Louvre Museum", "Tunis", title_pages=[page("Louvre", "A museum in Paris.")]).status == "rejected"


def test_missing_page_from_title_lookup_is_rejected():
    missing = {"title": "Museum of Invisible Spoons in Tunis", "missing": True}
    assert verify("Museum of Invisible Spoons", "Tunis", title_pages=[missing]).status == "rejected"


def test_wikidata_supplies_missing_coordinates():
    pages = [page("Bardo National Museum", "A museum in Tunis.", pageprops={"wikibase_item": "Q1"})]
    claims = {"claims": {"P625": [{"mainsnak": {"datavalue": {"value": {"latitude": 36.8, "longitude": 10.1}}}}]}}
    result = verify("Bardo Museum", "Tunis", pages=pages, wikidata=claims)
    assert (result.place.latitude, result.place.longitude) == (36.8, 10.1)


def test_missing_coordinates_and_views_are_reported_not_invented():
    result = verify("Bardo National Museum", "Tunis", pages=[page("Bardo National Museum", "In Tunis.")], views_status=404)
    assert result.status == "verified"
    assert result.place.latitude is None
    assert result.place.page_views is None
    assert result.place.missing == ["coordinates", "page_views"]


def test_views_come_from_the_last_full_month():
    seen = []
    pages = [page("Paris Museum Of Art", "In Paris.", coordinates=[{"lat": 1, "lon": 2}])]
    verify("Paris Museum Of Art", "Paris", pages=pages, seen=seen)
    assert seen[-1].endswith("/Paris_Museum_Of_Art/monthly/2026020100/2026022800")


def test_server_error_is_unavailable_not_rejected():
    assert verify("Louvre", "Paris", wiki_status=503).status == "unavailable"


def test_empty_input_is_rejected():
    assert verify(" ", "Paris").status == "rejected"
