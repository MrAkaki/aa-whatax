"""Settings accessors with safe defaults (TECHNICAL.md §16).

**Config-in-app, not settings.** Operator-facing configuration — Janice API key,
tax rates, payment corp/wallet, webhook, exclusions, good-ore, the reprocessing
yield, and the Janice mineral price basis — lives on the DB config models
(``TaxConfiguration`` et al., §5.1) and is edited in the app's **Admin tab**
(§15.3). This module carries only the handful of pure-operational, deploy-time
knobs that have no business in a UI.

Notably **not** here (deliberately):
- the **Janice API key** — moved to ``TaxConfiguration.janice_api_key`` (§5.1).
- **reprocessing yield** / **mineral price basis** — moved to
  ``TaxConfiguration`` (Admin tab). The constants below are only *seed defaults*
  for a freshly-created config row.
- a **timezone** — Whale Tax always uses EVE time (= UTC), fixed in code
  (``core/timeutils.py``); there is deliberately no ``WHATAX_TIMEZONE``.

Read everything through these accessors so defaults live in exactly one place.
"""

from decimal import Decimal

from django.conf import settings


def _get(name, default):
    return getattr(settings, name, default)


# Janice REST base URL (confirmed against the v2 OpenAPI spec, §7). Auth is the
# ``X-ApiKey`` header; the key itself is stored in the DB, never here.
WHATAX_JANICE_BASE_URL = _get("WHATAX_JANICE_BASE_URL", "https://janice.e-351.com/api/rest/v2")

# Janice market id used for pricing (2 = Jita per the v2 API).
WHATAX_JANICE_MARKET_ID = int(_get("WHATAX_JANICE_MARKET_ID", 2))

# Mineral-price cache TTL, seconds. Price drift within a month is acceptable and
# the monthly run is a single point-in-time, so a few hours caches well.
WHATAX_PRICE_CACHE_TTL = int(_get("WHATAX_PRICE_CACHE_TTL", 3600))

# ESI observer-ledger depth, days (the endpoint returns ~30 days).
WHATAX_LEDGER_LOOKBACK_DAYS = int(_get("WHATAX_LEDGER_LOOKBACK_DAYS", 30))

# Good-ore fraction at which a moon is considered "dead".
WHATAX_DEAD_THRESHOLD = Decimal(str(_get("WHATAX_DEAD_THRESHOLD", "0.95")))

# --- Seed defaults for a new TaxConfiguration row (NOT the runtime authority) ---

# Refined-value efficiency factor seed; the live value lives on
# ``TaxConfiguration.reprocessing_yield`` and is recorded on each snapshot.
REPROCESSING_YIELD_DEFAULT = Decimal(str(_get("WHATAX_REPROCESSING_YIELD_DEFAULT", "0.906")))

# Janice mineral price basis seed; the live value lives on
# ``TaxConfiguration.mineral_price_basis`` (one of TaxConfiguration.PriceBasis).
MINERAL_PRICE_BASIS_DEFAULT = _get("WHATAX_MINERAL_PRICE_BASIS_DEFAULT", "split_immediate")
