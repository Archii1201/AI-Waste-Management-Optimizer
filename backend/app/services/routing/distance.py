"""Travel-cost matrices for the routing solver.

Two sources, in order of preference:

* **OSRM** — real road distances and durations. A straight-line matrix badly
  misprices a city like Mumbai, where two bins 400m apart across a rail line
  can be a 6km drive, and a plan built on that lies to the driver.
* **Haversine** — straight-line distance inflated by a detour factor. Used when
  OSRM is disabled, unreachable, or the request exceeds its table size limit.
  Never fails, so route planning always produces something.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.geo import haversine_km

logger = get_logger(__name__)

# Ratio of real driving distance to straight-line distance in dense urban road
# networks. Applied so the fallback under-promises rather than over-promises.
URBAN_DETOUR_FACTOR = 1.35

# OSRM's public demo server rejects very large tables; stay well inside it.
MAX_OSRM_COORDINATES = 100

OSRM_TIMEOUT_SECONDS = 20.0


@dataclass
class TravelMatrix:
    """Pairwise distances (km) and durations (minutes) over an ordered point list."""

    distance_km: list[list[float]]
    duration_minutes: list[list[float]]
    source: str

    @property
    def size(self) -> int:
        return len(self.distance_km)

    def route_distance(self, order: list[int]) -> float:
        return sum(
            self.distance_km[order[i]][order[i + 1]] for i in range(len(order) - 1)
        )


def haversine_matrix(
    points: list[tuple[float, float]], *, avg_speed_kmph: float
) -> TravelMatrix:
    size = len(points)
    distances = [[0.0] * size for _ in range(size)]
    durations = [[0.0] * size for _ in range(size)]

    for i in range(size):
        for j in range(i + 1, size):
            straight = haversine_km(points[i][0], points[i][1], points[j][0], points[j][1])
            driving = straight * URBAN_DETOUR_FACTOR
            minutes = driving / max(1.0, avg_speed_kmph) * 60.0

            distances[i][j] = distances[j][i] = round(driving, 4)
            durations[i][j] = durations[j][i] = round(minutes, 3)

    return TravelMatrix(distances, durations, source="haversine")


def osrm_matrix(points: list[tuple[float, float]]) -> TravelMatrix | None:
    """Fetch a road-network matrix from OSRM, or None if unavailable."""
    if not settings.osrm_enabled or len(points) > MAX_OSRM_COORDINATES:
        return None

    # OSRM takes lon,lat — the reverse of every other coordinate in this codebase.
    coordinates = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"{settings.osrm_base_url.rstrip('/')}/table/v1/driving/{coordinates}"

    try:
        response = httpx.get(
            url,
            params={"annotations": "distance,duration"},
            timeout=OSRM_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OSRM matrix unavailable (%s); falling back to haversine", exc)
        return None

    if payload.get("code") != "Ok":
        logger.warning("OSRM returned %s; falling back to haversine", payload.get("code"))
        return None

    raw_distances = payload.get("distances")
    raw_durations = payload.get("durations")
    if not raw_distances or not raw_durations:
        return None

    # OSRM emits null for unreachable pairs; substituting a straight-line
    # estimate keeps the matrix solvable instead of crashing the solver.
    distances = [
        [
            round((value if value is not None else _fallback_metres(points, i, j)) / 1000.0, 4)
            for j, value in enumerate(row)
        ]
        for i, row in enumerate(raw_distances)
    ]
    durations = [
        [round((value if value is not None else 0.0) / 60.0, 3) for value in row]
        for row in raw_durations
    ]

    return TravelMatrix(distances, durations, source="osrm")


def _fallback_metres(points: list[tuple[float, float]], i: int, j: int) -> float:
    return (
        haversine_km(points[i][0], points[i][1], points[j][0], points[j][1])
        * URBAN_DETOUR_FACTOR
        * 1000.0
    )


def build_matrix(
    points: list[tuple[float, float]], *, avg_speed_kmph: float, prefer_osrm: bool = True
) -> TravelMatrix:
    """Road distances when possible, straight-line when not."""
    if prefer_osrm:
        matrix = osrm_matrix(points)
        if matrix is not None:
            return matrix
    return haversine_matrix(points, avg_speed_kmph=avg_speed_kmph)
