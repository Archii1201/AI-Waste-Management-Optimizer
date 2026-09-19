"""Geospatial helpers.

Distances are computed in Python with the haversine formula rather than in the
database, which keeps the project free of the PostGIS extension that managed
PostgreSQL tiers do not always offer. At city scale (hundreds of bins) the cost
is negligible, and the routing step replaces these straight-line distances with
real road distances from OSRM anyway.
"""

from __future__ import annotations

from math import asin, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = (
        sin(d_lat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Latitude/longitude box enclosing a radius.

    Used to pre-filter candidate rows with an indexable SQL range before the
    exact haversine check, so a radius query never scans the whole table.

    Returns (min_lat, max_lat, min_lon, max_lon).
    """
    lat_delta = degrees(radius_km / EARTH_RADIUS_KM)

    # Longitude degrees shrink toward the poles; guard against division by zero
    # in the degenerate case of a point at the pole itself.
    cos_lat = cos(radians(lat))
    lon_delta = degrees(radius_km / (EARTH_RADIUS_KM * cos_lat)) if abs(cos_lat) > 1e-9 else 180.0

    return (
        max(-90.0, lat - lat_delta),
        min(90.0, lat + lat_delta),
        max(-180.0, lon - lon_delta),
        min(180.0, lon + lon_delta),
    )
