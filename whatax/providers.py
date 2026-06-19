"""External access singletons: ESI and Janice I/O isolated for mocking."""

import logging
from decimal import Decimal

import requests
from django.core.cache import cache

from whatax import __version__, app_settings

logger = logging.getLogger(__name__)

# django-esi 9.x aiopenapi3 client; scoped to the tags this app uses.
from esi.openapi_clients import ESIClientProvider  # noqa: E402

try:  # pin to the installed django-esi's compatibility date
    from esi import __esi_compatibility_date__ as _ESI_COMPATIBILITY_DATE
except ImportError:  # pragma: no cover - fallback for other django-esi builds
    _ESI_COMPATIBILITY_DATE = "2026-05-19"

esi = ESIClientProvider(
    compatibility_date=_ESI_COMPATIBILITY_DATE,
    ua_appname="aa-whatax",
    ua_version=__version__,
    ua_url="https://github.com/MrAkaki/aa-whatax",
    # Only the tags whose operations this app calls (see whatax/tasks.py).
    tags=["Corporation", "Industry", "Character", "Wallet"],
)


# Map a TaxConfiguration.PriceBasis value -> (response group, price field).
_BASIS_MAP = {
    "split_immediate": ("immediatePrices", "splitPrice"),
    "buy_immediate": ("immediatePrices", "buyPrice"),
    "sell_immediate": ("immediatePrices", "sellPrice"),
    "split_top5": ("top5AveragePrices", "splitPrice"),
    "buy_top5": ("top5AveragePrices", "buyPrice"),
    "sell_top5": ("top5AveragePrices", "sellPrice"),
}


class JaniceError(Exception):
    """Raised on any Janice failure so pricing fails loud, never billing zero."""


class JaniceClient:
    """Thin, testable wrapper over the Janice v2 /pricer endpoint."""

    def __init__(self, api_key: str, *, base_url: str | None = None, timeout: int = 30):
        self.api_key = api_key
        self.base_url = (base_url or app_settings.WHATAX_JANICE_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _cache_key(self, type_id: int, market: int, basis: str) -> str:
        return f"whatax:janice:{market}:{basis}:{type_id}"

    def prices(self, type_ids, *, market: int | None = None, basis: str) -> dict[int, Decimal]:
        """Return {type_id: price} under the given basis; cached, only misses hit the network."""
        market = market if market is not None else app_settings.WHATAX_JANICE_MARKET_ID
        if basis not in _BASIS_MAP:
            raise JaniceError(f"unknown price basis: {basis!r}")
        group, field = _BASIS_MAP[basis]

        wanted = [int(t) for t in type_ids]
        out: dict[int, Decimal] = {}
        misses: list[int] = []
        for tid in wanted:
            cached = cache.get(self._cache_key(tid, market, basis))
            if cached is not None:
                out[tid] = Decimal(cached)
            else:
                misses.append(tid)

        if misses:
            out.update(self._fetch(misses, market, basis, group, field))
        return out

    def _fetch(self, type_ids, market, basis, group, field) -> dict[int, Decimal]:
        if not self.api_key:
            raise JaniceError("Janice API key is not configured (set it in the Admin tab).")
        url = f"{self.base_url}/pricer"
        body = "\n".join(str(t) for t in type_ids)
        try:
            resp = requests.post(
                url,
                params={"market": market},
                headers={"X-ApiKey": self.api_key, "Content-Type": "text/plain"},
                data=body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise JaniceError(f"Janice request failed: {exc}") from exc
        if resp.status_code != 200:
            # Log only the status, never the key.
            raise JaniceError(f"Janice returned HTTP {resp.status_code}")
        try:
            items = resp.json()
        except ValueError as exc:
            raise JaniceError("Janice returned a non-JSON body") from exc

        result: dict[int, Decimal] = {}
        for item in items:
            try:
                tid = int(item["itemType"]["eid"])
                price = Decimal(str(item[group][field]))
            except (KeyError, TypeError, ValueError) as exc:
                raise JaniceError(f"unexpected Janice item shape: {exc}") from exc
            result[tid] = price
            cache.set(
                self._cache_key(tid, market, basis), str(price), app_settings.WHATAX_PRICE_CACHE_TTL
            )
        return result
