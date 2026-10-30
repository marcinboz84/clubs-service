"""
Proxy do nieoficjalnego API EA FC Pro Clubs dla OutSystems.

OutSystems -> (ten serwis) -> proclubs.ea.com

Zapytania do EA wysyła biblioteka fc-clubs-api (przeglądarkowe nagłówki),
a OutSystems woła zwykłe, czyste REST API tego serwisu.

Uruchomienie lokalne:
    pip install -r requirements.txt
    PROXY_API_KEY=jakis-dlugi-sekret uvicorn main:app --host 0.0.0.0 --port 8000
Dokumentacja Swagger: http://localhost:8000/docs
"""

import os
import threading
import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fc_clubs_api import FC27API, FC27APIError

# --- konfiguracja (zmienne środowiskowe) -----------------------------------
API_KEY = os.getenv("PROXY_API_KEY", "")          # sekret wysyłany z OutSystems
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))   # cache odpowiedzi EA
MIN_INTERVAL = float(os.getenv("MIN_INTERVAL_SECONDS", "1.0"))  # przerwa między zapytaniami do EA
EA_TIMEOUT = int(os.getenv("EA_TIMEOUT_SECONDS", "15"))
ALLOWED_PLATFORMS = {"common-gen5", "common-gen4"}
ALLOWED_MATCH_TYPES = {"leagueMatch", "friendlyMatch", "playoffMatch"}

app = FastAPI(title="Pro Clubs proxy", version="1.0.0")

# --- zabezpieczenie kluczem -----------------------------------------------
def check_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Jeśli PROXY_API_KEY jest ustawiony, wymagaj go w nagłówku X-Api-Key."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Nieprawidłowy lub brakujący X-Api-Key")


# --- klienci, cache i ograniczenie tempa zapytań ---------------------------
_clients: dict[str, FC27API] = {}
_cache: dict[tuple, tuple[float, Any]] = {}
_lock = threading.Lock()          # serializuje zapytania do EA
_last_call = 0.0


def _client(platform: str) -> FC27API:
    if platform not in ALLOWED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"platform musi być jednym z: {sorted(ALLOWED_PLATFORMS)}")
    if platform not in _clients:
        _clients[platform] = FC27API(platform=platform, timeout=EA_TIMEOUT)
    return _clients[platform]


def ea_get(platform: str, endpoint: str, params: dict) -> Any:
    """Pobiera surowy JSON z EA z cache'em i przerwą między zapytaniami."""
    global _last_call
    key = (platform, endpoint, tuple(sorted(params.items())))

    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]

        wait = MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            data = _client(platform).get_json(endpoint, params)
        except FC27APIError as e:
            raise HTTPException(status_code=502, detail=f"Błąd EA: {e}")
        finally:
            _last_call = time.time()

        _cache[key] = (time.time(), data)
        return data


# --- endpointy -------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/clubs/search", dependencies=[Depends(check_key)])
def search_club(
    clubName: str = Query(..., min_length=1),
    platform: str = "common-gen5",
    season: str = Query("all", pattern="^(all|current)$"),
    maxResultCount: int = Query(10, ge=1, le=50),
):
    """Szukanie klubu po nazwie (zwraca m.in. clubId). season: all | current."""
    endpoint = "allTimeLeaderboard/search" if season == "all" else "currentSeasonLeaderboard/search"
    return ea_get(platform, endpoint, {"clubName": clubName, "maxResultCount": maxResultCount})


@app.get("/clubs/info", dependencies=[Depends(check_key)])
def club_info(clubIds: str, platform: str = "common-gen5"):
    """Profil klubu (odpowiednik Twojego GetProfile). clubIds może być oddzielone przecinkami."""
    return ea_get(platform, "clubs/info", {"clubIds": clubIds})


@app.get("/clubs/overall-stats", dependencies=[Depends(check_key)])
def club_overall_stats(clubIds: str, platform: str = "common-gen5"):
    return ea_get(platform, "clubs/overallStats", {"clubIds": clubIds})


@app.get("/clubs/matches", dependencies=[Depends(check_key)])
def club_matches(
    clubIds: str,
    platform: str = "common-gen5",
    matchType: str = "leagueMatch",
    maxResultCount: int = Query(10, ge=1, le=50),
):
    if matchType not in ALLOWED_MATCH_TYPES:
        raise HTTPException(status_code=400, detail=f"matchType musi być jednym z: {sorted(ALLOWED_MATCH_TYPES)}")
    return ea_get(platform, "clubs/matches",
                  {"clubIds": clubIds, "matchType": matchType, "maxResultCount": maxResultCount})


@app.get("/members/stats", dependencies=[Depends(check_key)])
def members_stats(clubId: str, platform: str = "common-gen5"):
    return ea_get(platform, "members/stats", {"clubId": clubId})


@app.get("/members/career-stats", dependencies=[Depends(check_key)])
def members_career_stats(clubId: str, platform: str = "common-gen5"):
    return ea_get(platform, "members/career/stats", {"clubId": clubId})


@app.get("/clubs/playoff-achievements", dependencies=[Depends(check_key)])
def playoff_achievements(clubId: str, platform: str = "common-gen5"):
    return ea_get(platform, "club/playoffAchievements", {"clubId": clubId})
