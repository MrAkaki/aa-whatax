"""Settings accessors with safe defaults; operator config lives on DB models."""

from decimal import Decimal

from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


# Janice REST base URL; key is stored in the DB, never here.
WHATAX_JANICE_BASE_URL = _get("WHATAX_JANICE_BASE_URL", "https://janice.e-351.com/api/rest/v2")

# Janice market id used for pricing (2 = Jita).
WHATAX_JANICE_MARKET_ID = int(_get("WHATAX_JANICE_MARKET_ID", 2))

# Mineral-price cache TTL, seconds.
WHATAX_PRICE_CACHE_TTL = int(_get("WHATAX_PRICE_CACHE_TTL", 3600))

# ESI observer-ledger depth, days.
WHATAX_LEDGER_LOOKBACK_DAYS = int(_get("WHATAX_LEDGER_LOOKBACK_DAYS", 30))

# Good-ore fraction at which a moon is considered "dead".
WHATAX_DEAD_THRESHOLD = Decimal(str(_get("WHATAX_DEAD_THRESHOLD", "0.95")))

# --- Seed defaults for a new TaxConfiguration row (not the runtime authority) ---

# Refined-value efficiency factor seed.
REPROCESSING_YIELD_DEFAULT = Decimal(str(_get("WHATAX_REPROCESSING_YIELD_DEFAULT", "0.906")))

# Janice mineral price basis seed.
MINERAL_PRICE_BASIS_DEFAULT = _get("WHATAX_MINERAL_PRICE_BASIS_DEFAULT", "split_immediate")
