"""Does a free Foursquare key work, and does it find restaurants in very different cities?

Uses 3 of your free calls. The key comes from .env and is never printed.

    python -m spikes.check_foursquare
"""

from city_planner.tools.find_restaurants import find_restaurants
from spikes.common import require_env, save_report

CITIES = [("Paris", 48.8606, 2.3376), ("Tunis", 36.7975, 10.1714), ("Luang Prabang", 19.8910, 102.1340)]


def main() -> None:
    require_env("FOURSQUARE_API_KEY")
    reports = []
    for city, latitude, longitude in CITIES:
        result = find_restaurants(latitude, longitude, limit=5)
        names = [place.name for place in result.restaurants]
        reports.append({"city": city, "status": result.status, "note": result.note, "restaurants": names})
        print(f"{city}: {result.status}  {result.note or names}")
    print(f"\nSaved to {save_report('foursquare', {'cities': reports})}")


if __name__ == "__main__":
    main()
