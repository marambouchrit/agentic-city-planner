"""Straight-line distance and rough travel times. These are estimates, not real routes."""

import math

EARTH_RADIUS_KM = 6371.0
WALKING_SPEED_KMH = 4.5
TAXI_SPEED_KMH = 25.0  # low on purpose: city traffic, and roads are longer than a straight line


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def estimated_walking_minutes(distance_km: float) -> float:
    return distance_km / WALKING_SPEED_KMH * 60


def estimated_taxi_minutes(distance_km: float) -> float:
    return distance_km / TAXI_SPEED_KMH * 60
