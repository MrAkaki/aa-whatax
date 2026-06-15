# Whale Tax — Technical Design Document

> Companion to [RESEARCH.md](RESEARCH.md). RESEARCH.md is the *what/why*; this
> document is the *how*. It is the implementation specification for the
> `aa-whatax` Alliance Auth plugin.

| | |
|---|---|
| **Distribution (PyPI)** | `aa-whatax` |
| **Django app label** | `whatax` |
| **URL namespace** | `whatax:` |
| **Verbose name** | Whale Tax |
| **Status** | Implemented — initial full vertical (models + migration, ESI sync tasks, pricing, tax calc, payment matching, moon tracking, tabbed UI, unit tests) |

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Technology Stack](#2-technology-stack)
3. [Prior Art & Reuse](#3-prior-art--reuse)
4. [Package Layout](#4-package-layout)
5. [Data Model](#5-data-model)
6. [ESI Integration](#6-esi-integration)
7. [Pricing Service (Janice)](#7-pricing-service-janice)
8. [Mining Data Pipeline](#8-mining-data-pipeline)
9. [Tax Calculation](#9-tax-calculation)
10. [Payment Matching](#10-payment-matching)
11. [Moon Status Tracking](#11-moon-status-tracking)
12. [Notifications](#12-notifications)
13. [Scheduled Tasks (Celery Beat)](#13-scheduled-tasks-celery-beat)
14. [Permissions](#14-permissions)
15. [Views & UI](#15-views--ui)
16. [Settings](#16-settings)
17. [Installation](#17-installation)
18. [Testing Strategy](#18-testing-strategy)
19. [Risks & Open Questions](#19-risks--open-questions)

---

## 1. Architecture Overview

Whale Tax is a standard Alliance Auth (AA) community plugin: a Django app that
plugs into an existing AA installation via AA's hook system. It owns no user or
character identity of its own — it consumes AA's identity model and EVE static
data, and adds tax/accounting domain logic on top.

```
                         ┌──────────────────────────────────────────┐
                         │            Alliance Auth core             │
                         │  User · CharacterOwnership · EveCharacter  │
                         │  EveCorporationInfo · UserProfile (main)   │
                         └───────────────▲──────────────────────────┘
                                         │ identity / main-char resolution
                                         │
   EVE ESI ──┐                  ┌────────┴─────────┐                  ┌── Janice API
  (mining,   │   django-esi     │     whatax       │   price service  │  (refined
  wallet,    ├────────────────► │  models · tasks  │ ◄────────────────┤   values)
  extractions│  (tokens)        │  views · hooks   │                  └──
  structures)│                  └───┬────────┬─────┘
             │                      │        │
  django-eveuniverse ──────────────┘        └───────► Discord (webhook + DM)
  (EveType, EveTypeMaterial, EveMoon)
```

**Control flow is task-driven.** Almost nothing happens in a request/response
cycle. Celery beat fires periodic tasks that (a) pull ESI data into raw tables,
(b) aggregate and price it, (c) calculate tax, (d) reconcile wallet payments,
and (e) emit notifications. The web UI is the **single configuration surface**:
*all* setup — API keys, tax rates, per-corp overrides, moon exclusions — is done
in the plugin's own tabbed site pages, **not** the Django admin. Beyond config,
the UI is read-mostly: staff review and fix payments, members view their own
frags, mining, and liability.

**Tabbed UI, three roles** ([§14](#14-permissions)/[§15](#15-views--ui)). The app
presents as permission-gated tabs:

- **User** (`basic_access`) — a simple **Dashboard**: current + upcoming (2-day)
  frags, own mining last week / month, own tax record (charges, balances,
  payments).
- **Staff** (`manage_payments`) — adds a **Staff** tab: fix payments, add/remove
  balances across all players. Extensible with more actions later.
- **Admin** (`admin_access`) — adds the **Admin** tab holding every dangerous
  control (keys, rates, exclusions, calc).

The Django admin is *not* part of the operator workflow — it exists only as a
low-level fallback. Configuration is stored in the DB and edited through the app,
never in `local.py` (with the deliberate exception of a few pure-operational
knobs in [§16](#16-settings)).

**Two-tier data model.** Every external data source is landed in a *raw* table
first (idempotent upsert keyed on the source's own ID), then *derived* tables
are computed from raw rows. This makes every pipeline stage re-runnable and
makes retroactive correction possible (re-aggregate from raw without re-hitting
ESI).

---

## 2. Technology Stack

| Concern | Choice | Notes |
|---|---|---|
| Framework | Django (AA-pinned version) | Follow the Django version AA currently requires; do not pin independently. |
| App scaffold | `cookiecutter-allianceauth-app` | Generates the canonical AA app skeleton. |
| ESI access | `django-esi` | Token storage, scope enforcement, auto-refresh, swagger client. |
| EVE static data | `django-eveuniverse` | `EveType`, `EveTypeMaterial` (reprocessing yield), `EveMoon`, `EveSolarSystem`. Avoids shipping our own SDE. |
| Async / scheduling | Celery + `django-celery-beat` | Already part of every AA deploy. |
| Discord | `dhooks-lite` (webhook), AA notifications / `aa-discordnotify` (DM) | DM requires the Discord service to be active. |
| HTTP (Janice) | `requests` | Behind a thin provider class for testability. |
| Pricing input | Janice API + `EveTypeMaterial` | See [§7](#7-pricing-service-janice). |

Money is stored as `DecimalField(max_digits=20, decimal_places=2)`. ISK can
exceed trillions; 20 digits leaves headroom. **Never use float for ISK.**
Tax rates are `DecimalField(max_digits=5, decimal_places=4)` (e.g. `0.1000` =
10%).

---

## 3. Prior Art & Reuse

**Decision: Whale Tax is self-contained and does *not* depend on `aa-moonmining`.**
`aa-moonmining` (Erik Kalkoken) is a solid app and covers much of the same
moon-side ground (structure discovery, the observer ledger, extraction tracking,
ore value estimation), but Whale Tax owns its full data model for **flexibility**:

- **No coupling** to another app's schema, migrations, or release cadence — our
  tax/payment domain drives the moon schema, not the other way around.
- **Global default + per-structure** good-ore configuration, our own
  extraction/composition storage, and freedom to extend matching, pricing, and
  notification logic without working around an upstream model.
- **Single install surface** — one app to deploy, one set of ESI tokens/scopes
  to reason about, no version-compatibility matrix against `aa-moonmining`.

The one thing `aa-moonmining` "solved" that we'd otherwise re-derive — chunk ore
composition / volume — we get for free: it is carried in the
`MoonminingExtractionStarted` corp notification (`oreVolumeByType`), which we
already ingest for pop detection ([§11](#11-moon-status-tracking)). So going
independent costs us no extra ESI plumbing.

`aa-moonmining` remains useful as a **reference implementation** for the
notification-YAML parsing details — read it for the field shapes, don't import
it.

The **tax, payment-matching, and notification** layers are Whale Tax's own
contribution and have no close prior art in the AA ecosystem.

---

## 4. Package Layout

```
aa-whatax/
├── whatax/
│   ├── __init__.py            # __version__ = "x.y.z"
│   ├── apps.py                # WhataxConfig(AppConfig)
│   ├── auth_hooks.py          # MenuItemHook + UrlHook registration
│   ├── app_settings.py        # getattr(settings, ...) with defaults
│   ├── urls.py
│   ├── views.py               # thin; delegates to services
│   ├── admin.py
│   ├── models.py              # see §5
│   ├── managers.py            # QuerySet/Manager logic kept off views
│   ├── providers.py           # ESI client + Janice client singletons
│   ├── core/
│   │   ├── pricing.py         # refined-value computation
│   │   ├── aggregation.py     # ledger → per-player snapshot
│   │   ├── tax.py             # rate resolution + tax calc
│   │   ├── matching.py        # wallet → tax record matching
│   │   └── moons.py           # dead/pop detection
│   ├── tasks.py               # @shared_task wrappers around core/*
│   ├── notifications.py       # discord webhook + DM dispatch
│   ├── migrations/
│   ├── templates/whatax/
│   ├── static/whatax/
│   └── tests/
├── pyproject.toml
├── README.md
├── RESEARCH.md
└── TECHNICAL.md
```

**Layering rule:** `views` and `tasks` are thin entry points. All domain logic
lives in `core/` as plain functions/classes that take models and return values,
so it is unit-testable without a request or a worker. ESI/HTTP access is
isolated in `providers.py` so it can be mocked in one place.

---

## 5. Data Model

Conventions: every raw table has a unique key on the *source* identifier for
idempotent upsert; derived tables have `unique_together` on their natural key;
all FKs to AA/eveuniverse use `on_delete=PROTECT` unless cascade is clearly
correct.

### 5.1 Configuration

**`TaxConfiguration`** — singleton (enforce `pk=1`). Edited entirely from the
**Admin** tab ([§15](#15-views--ui)); this row *is* the app's config.

| Field | Type | Notes |
|---|---|---|
| `default_tax_rate` | Decimal(5,4) | Global / general rate, e.g. `0.1000`. Per-corp overrides win ([`CorporationTaxRate`](#51-configuration)). |
| `payment_corporation` | FK `EveCorporationInfo` PROTECT | The corp players pay **to**; its wallet is monitored. A player may pay from **any** of their registered characters ([§10](#10-payment-matching)). |
| `payment_wallet_division` | PositiveSmallInt | 1–7. |
| `broadcast_webhook_url` | URLField | Default Discord webhook for corp-wide events. |
| `janice_api_key` | Char(blank) | **Set in the Admin tab**, stored in the DB (see [§7](#7-pricing-service-janice) / [§16](#16-settings) security note). |
| `reprocessing_yield` | Decimal(5,4) | Refined-value efficiency factor (e.g. `0.9060`). **Edited in the Admin tab** (moved out of settings); recorded on each snapshot for reproducibility. |
| `mineral_price_basis` | Char choices | Which Janice figure values minerals: `split/buy/sell` × `immediate/top5` (default `split_immediate`). **Edited in the Admin tab**; recorded on each snapshot. |
| `grace_period_days` | PositiveSmallInt | Pay-by window: `due_date = emitted_at + grace_period_days`; a record past it with balance owed becomes `overdue`. |
| `tax_edit_window_days` | PositiveSmallInt | Days after `emitted_at` during which **staff** may edit a bill's `tax_due` (default `15`, [§5.4](#54-payments) `TaxRecordEdit`). |
| `exclude_highsec` | Bool | Exclude all high-sec (≥0.5) mining from tax. |
| `exclude_lowsec` | Bool | Exclude all low-sec (0.1–0.45) mining from tax. |
| `exclude_nullsec` | Bool | Exclude all null-sec (≤0.0) mining from tax. |
| `is_enabled` | Bool | Master kill-switch for scheduled work. |

> **Secrets in the DB.** Moving the Janice key out of `local.py` and into the
> Admin tab is a deliberate UX choice (one config surface, no shell access
> needed). The cost: the key now lives in the database. Mitigate by gating the
> Admin tab behind `admin_access` ([§14](#14-permissions)), never rendering the
> stored key back to the page (write-only field; show "set / not set"), and
> keeping it out of logs and the Django admin list display.

**`CorporationTaxRate`** — per-corp override.

| Field | Type | Notes |
|---|---|---|
| `corporation` | OneToOne `EveCorporationInfo` PROTECT | |
| `tax_rate` | Decimal(5,4) | Applies to players whose **main** char is in this corp. |
| `flat_discount` | Decimal(20,2) | ISK subtracted from each member's monthly charge; the charge floors at 0. Default 0. |
| `note` | Char | Optional audit note. |

**`MiningStructure`** — a corp-owned refinery/drill (an ESI mining *observer*).

| Field | Type | Notes |
|---|---|---|
| `structure_id` | BigInt unique | ESI structure ID; doubles as `observer_id`. |
| `corporation` | FK `EveCorporationInfo` PROTECT | Owner. |
| `name` | Char | From ESI structures endpoint. |
| `eve_moon` | FK `EveMoon` PROTECT null | Anchored moon, if resolvable. |
| `eve_solar_system` | FK `EveSolarSystem` PROTECT null | Resolved from the structure/moon; carries `security_status` → sec-class for exclusions. |
| `eve_type` | FK `EveType` PROTECT null | Structure type. |
| `is_active` | Bool | **Per-structure exclusion toggle** (Admin tab). `False` = exclude this structure's mining from tax. |
| `last_ledger_sync` | DateTime null | Watermark. |

**`GoodOreDefault`** — the global good-ore set: ores that "count" at every
structure by default. Seeded with all moon ore types by `whatax_seed_good_ores`.

| Field | Type | Notes |
|---|---|---|
| `ore_type` | FK `EveType` PROTECT, unique | |

**`StructureGoodOre`** — per-structure override of the global default.

| Field | Type | Notes |
|---|---|---|
| `structure` | FK `MiningStructure` CASCADE | |
| `ore_type` | FK `EveType` PROTECT | |
| `include` | Bool | `True` = good here even if not a global default; `False` = exclude this default ore here. |
| — | | `unique_together(structure, ore_type)` |

The **effective** good-ore set for a structure is `good_ore_ids_for()`:
`(global defaults − structure excludes) ∪ structure includes`. Resolved live, so
edits to either list apply to existing extractions on the next `recompute_dead`.

**Moon / mining exclusions.** Configured in the Admin tab, applied as a filter
in the tax pipeline ([§9](#9-tax-calculation)). Two independent, OR-combined
levers — mining is excluded if *either* matches:

1. **By security class** — `exclude_highsec` / `exclude_lowsec` / `exclude_nullsec`
   on `TaxConfiguration`. A structure's class comes from
   `eve_solar_system.security_status`: HS ≥ 0.5, LS 0.1–0.45 (rounded), NS ≤ 0.0.
   A helper `core/moons.py:sec_class(security_status)` is the single source of the
   banding so calc and UI agree.
2. **By structure** — `MiningStructure.is_active = False`. Lets an officer carve
   out one refinery regardless of its sec class.

Exclusion suppresses *tax liability* only; raw ledger rows are still synced and
stored (we never drop ESI data), so toggling an exclusion and re-running calc is
fully retroactive within the period. Excluded mining is shown but untaxed in the
officer drilldown so the carve-out is auditable.

> 🔌 **Self-contained — no shared tables.** `MiningStructure` is populated by
> our own `sync_structures` task straight from
> `GET /corporations/{id}/structures/` ([§6.2](#62-endpoints-consumed)); it is
> *not* a read/proxy of `aa-moonmining`'s structure/refinery models. We never
> join across another app's schema or rely on its migrations. The only external
> tables Whale Tax reads are AA core identity (`User`, `CharacterOwnership`,
> `EveCharacter`, `EveCorporationInfo`) and `django-eveuniverse` static data
> (`EveType`, `EveMoon`) — both stable, shared platform layers, not app peers.

### 5.2 Periods & Raw Ledger

**`TaxPeriod`** — one calendar month.

| Field | Type | Notes |
|---|---|---|
| `year` / `month` | PositiveSmallInt | `unique_together(year, month)`. |
| `period_start` / `period_end` | DateTime | UTC bounds (see [§9](#9-tax-calculation)). |
| `state` | Char choices | `open → calculating → finalized → closed`. |
| `calculated_at` | DateTime null | |

**`MiningLedgerEntry`** — raw observer rows (the source of truth for mining).

| Field | Type | Notes |
|---|---|---|
| `structure` | FK `MiningStructure` CASCADE | |
| `character_id` | BigInt | ESI gives ID; resolve lazily. |
| `ore_type` | FK `EveType` PROTECT | |
| `quantity` | BigInt | Units mined. |
| `recorded_date` | Date | Observer `last_updated` (a date, not a timestamp). |
| `recorded_corporation_id` | BigInt | From observer payload. |
| — | | `unique_together(structure, character_id, ore_type, recorded_date)` |

> ⚠️ The observer ledger is **daily-granular and ~30 days deep**. A row is the
> *cumulative* total for that character/ore/structure on that date. Treat the
> daily row as authoritative for its date and upsert on the unique key; do not
> sum the same date twice. See [§8](#8-mining-data-pipeline) and
> [§19](#19-risks--open-questions).

> 🔌 **Self-contained — no shared tables.** `MiningLedgerEntry` is Whale Tax's own
> raw landing table, written by `sync_mining_ledger` from the observer endpoint
> ([§6.2](#62-endpoints-consumed)). It is the **single source of truth for
> mining** for *both* the tax pipeline (numerator of the bill) and moon
> dead-detection (numerator of the dead %), so the two features never disagree
> about who mined what. We do not import `aa-moonmining`'s `MiningLedgerRecord`
> or read its rows — owning this table is what lets us key, index, and re-sync it
> on our own terms.

### 5.3 Derived: Snapshots & Tax

**`MiningSnapshot`** — aggregated mining per player/ore/period (derived from
ledger entries).

| Field | Type | Notes |
|---|---|---|
| `tax_period` | FK `TaxPeriod` CASCADE | |
| `user` | FK `auth.User` PROTECT | The **player** (resolved owner). |
| `ore_type` | FK `EveType` PROTECT | |
| `quantity` | BigInt | Summed across all alts + structures. |
| `refined_value` | Decimal(20,2) | Snapshot of value at calc time. |
| `reprocessing_yield_applied` | Decimal(5,4) null | Yield factor used at calc time (reproducibility). |
| `price_basis_applied` | Char | Mineral price basis used at calc time (reproducibility). |
| `is_excluded` | Bool | Mining counted but **not taxed** (sec-class / structure exclusion). Shown untaxed in the drilldown. |
| — | | `unique_together(tax_period, user, ore_type, is_excluded)` — the excluded bucket is a separate priced row so a player mining the same ore at a taxed *and* an excluded structure is represented correctly; tax sums only `is_excluded=False`. |

**`TaxRecord`** — the bill, per player/period. Emitted on the **1st of the month**
for the *previous* month ([§9](#9-tax-calculation)/[§13](#13-scheduled-tasks-celery-beat)).

| Field | Type | Notes |
|---|---|---|
| `tax_period` | FK `TaxPeriod` CASCADE | |
| `user` | FK `auth.User` PROTECT | |
| `total_mined_value` | Decimal(20,2) | Σ of snapshot refined values. |
| `tax_rate_applied` | Decimal(5,4) | Frozen at calc time. |
| `flat_discount_applied` | Decimal(20,2) | Corp flat discount actually subtracted at calc (≤ gross). Frozen for transparency; the bill reconciles as `total × rate − flat_discount_applied`. |
| `original_tax_due` | Decimal(20,2) | `max(0, total_mined_value × rate − flat_discount_applied)` at emission — **immutable** audit baseline (post-discount). |
| `tax_due` | Decimal(20,2) | **Effective** charge. Equals `original_tax_due` unless a staff market-correction edit applied ([§5.4](#54-payments) `TaxRecordEdit`). |
| `amount_paid` | Decimal(20,2) | Σ matched payments. |
| `emitted_at` | DateTime null | When the bill was emitted (the monthly run). Drives the pay-by `due_date` and the **15-day staff edit window**. |
| `due_date` | DateTime null | `emitted_at + TaxConfiguration.grace_period_days`. Frozen at emission so the deadline can't shift if config changes later. |
| `corporation_at_calc` | FK `EveCorporationInfo` PROTECT null | Main-char corp snapshot (audit trail for which rate applied). |
| `status` | Char choices | `pending · partial · paid · waived · overdue`. |
| `notified_due_at` | DateTime null | Idempotency for "tax due" notification. |
| — | | `unique_together(tax_period, user)` |

> **Balance sign convention.** Emitting a bill creates a **negative balance** (the
> player *owes*); payments and credits move it toward zero / positive:
> `balance = amount_paid + Σ adjustments − tax_due`. Negative = outstanding,
> `0` = settled, positive = overpaid/credit. The Dashboard and Staff tabs show
> this signed balance directly.

### 5.4 Payments

**`WalletJournalEntry`** — raw corp wallet journal rows.

| Field | Type | Notes |
|---|---|---|
| `entry_id` | BigInt unique | ESI journal `id` — globally unique, primary idempotency key. |
| `division` | PositiveSmallInt | |
| `ref_type` | Char | e.g. `player_donation`. |
| `amount` | Decimal(20,2) | Positive = inflow. |
| `balance` | Decimal(20,2) | |
| `date` | DateTime | |
| `first_party_id` | BigInt | Sender (character/corp ID). |
| `second_party_id` | BigInt | Receiver. |
| `reason` | Text | Free-text memo (matching hint). |
| `is_processed` | Bool | Matching pass completed. |

**`Payment`** — a matched (or unmatched) inflow attributable to a player.

| Field | Type | Notes |
|---|---|---|
| `journal_entry` | OneToOne `WalletJournalEntry` PROTECT | |
| `tax_record` | FK `TaxRecord` SET_NULL null | Null = unmatched/unallocated. |
| `character_id` | BigInt | Resolved payer (= `first_party_id`). |
| `user` | FK `auth.User` SET_NULL null | Resolved player. |
| `amount` | Decimal(20,2) | |
| `date` | DateTime | |
| `match_method` | Char choices | `auto · manual · unmatched`. |
| `notified_at` | DateTime null | Idempotency for "payment received". |

**`BalanceAdjustment`** — a staff manual credit/debit on a record (the "add or
remove balance" action, [§14](#14-permissions)). Kept separate from `Payment` so
the wallet-derived ledger stays clean and every manual change is attributable.

| Field | Type | Notes |
|---|---|---|
| `tax_record` | FK `TaxRecord` CASCADE | Target bill. |
| `amount` | Decimal(20,2) | Signed: positive = credit (reduces owed), negative = debit. |
| `reason` | Text | Required audit note. |
| `created_by` | FK `auth.User` PROTECT | Staff member who made it. |
| `created_at` | DateTime auto | |

> Balance (signed, negative = owed) = `amount_paid + Σ adjustment.amount − tax_due`.
> Status recompute ([§10](#10-payment-matching)) treats `amount_paid + Σ adjustments`
> as the settled total, so a manual credit can move a record to `paid`/`partial`
> exactly like a real payment — but never overwrites the wallet-matched figures.

**`TaxRecordEdit`** — audit log for a staff **tax-amount correction** (distinct
from a balance adjustment: this changes the *charge* `tax_due`, not the
payment/credit side). Motivated by ore-price market manipulation that can make a
month's computed value briefly wrong.

| Field | Type | Notes |
|---|---|---|
| `tax_record` | FK `TaxRecord` CASCADE | |
| `old_tax_due` | Decimal(20,2) | Value before the edit. |
| `new_tax_due` | Decimal(20,2) | Value after the edit (written to `TaxRecord.tax_due`). |
| `reason` | Text | Required (e.g. "Veldspar price spike — manipulation"). |
| `edited_by` | FK `auth.User` PROTECT | Staff member. |
| `edited_at` | DateTime auto | |

> ⏱️ **15-day window.** A tax edit is only permitted while
> `now ≤ emitted_at + TaxConfiguration.tax_edit_window_days` (default **15**). The
> guard lives in `core/tax.py` and is enforced server-side, not just hidden in
> the UI. `original_tax_due` is never touched, so the manipulated-vs-corrected
> delta stays auditable forever; each edit appends a `TaxRecordEdit` row.

### 5.5 Moon Tracking

**`MoonExtraction`** (a.k.a. MoonCycle) — one extraction cycle for a structure.

| Field | Type | Notes |
|---|---|---|
| `structure` | FK `MiningStructure` CASCADE | |
| `eve_moon` | FK `EveMoon` PROTECT null | |
| `extraction_start_time` | DateTime | From extractions endpoint. |
| `chunk_arrival_time` | DateTime | When the chunk is mineable. |
| `natural_decay_time` | DateTime | Auto-fracture deadline. |
| `status` | Char choices | `scheduled · active · popped · dead · cancelled`. |
| `popped_at` | DateTime null | Set on fracture event. |
| `total_good_ore_m3` | Decimal(24,2) null | Dead-% denominator; Σ good-ore volume from `oreVolumeByType` (see [§11](#11-moon-status-tracking)). |
| `mined_good_ore_m3` | Decimal(24,2) | Running good-ore volume mined (ledger units × `EveType.volume`). |
| `dead_at` | DateTime null | When ≥95% threshold crossed. |
| `notified_pop_at` / `notified_dead_at` | DateTime null | Notification idempotency. |
| — | | `unique_together(structure, chunk_arrival_time)` |

**`ExtractionOre`** — per-extraction ore composition, snapshotted from the
`MoonminingExtractionStarted` notification. This is what makes dead-detection
self-sufficient (no external scan data / no `aa-moonmining`).

| Field | Type | Notes |
|---|---|---|
| `extraction` | FK `MoonExtraction` CASCADE | |
| `ore_type` | FK `EveType` PROTECT | |
| `volume_m3` | Decimal(24,2) | Chunk volume of this ore from `oreVolumeByType`. |
| `is_good_ore` | Bool | Good-ore membership snapshotted at extraction start. |
| — | | `unique_together(extraction, ore_type)` |

---

## 6. ESI Integration

All ESI access goes through `django-esi`: tokens are stored per-character with
their granted scopes; the client auto-refreshes. Whale Tax never handles refresh
tokens directly.

### 6.1 Required Scopes

| Endpoint | Scope | Granted by |
|---|---|---|
| Corp structures | `esi-corporations.read_structures.v1` | A director/structure-role char. |
| Mining observers (ledger) | `esi-industry.read_corporation_mining.v1` | Char with Accountant or Station Manager role. |
| Moon extractions | `esi-industry.read_corporation_mining.v1` | (same) |
| Corp wallet journal | `esi-wallet.read_corporation_wallets.v1` | Char with Accountant / Junior Accountant. |
| Corp notifications (moon events) | `esi-characters.read_notifications.v1` | Any corp member char (delivers corp notifications). |

> ESI corp endpoints additionally require the *in-game role* on the character
> behind the token (Director/Accountant/Station Manager). A token with the
> scope but without the role returns **403**. Surface this clearly in the UI
> as a distinct failure from "missing scope."

### 6.2 Endpoints Consumed

| Purpose | Endpoint (shape) |
|---|---|
| Discover structures | `GET /corporations/{corporation_id}/structures/` |
| List observers | `GET /corporation/{corporation_id}/mining/observers/` |
| Read a ledger | `GET /corporation/{corporation_id}/mining/observers/{observer_id}/` |
| Moon extractions | `GET /corporation/{corporation_id}/mining/extractions/` |
| Wallet journal | `GET /corporations/{corporation_id}/wallets/{division}/journal/` |
| Corp notifications | `GET /characters/{character_id}/notifications/` |

### 6.3 Pagination, Caching, Rate Limiting

- **Pagination:** observer-ledger, journal, and extractions are paginated via
  `X-Pages`. Loop pages until exhausted; respect the per-page `Expires`/`ETag`.
- **ETag/Expires:** store `ETag` per (endpoint, key) and send
  `If-None-Match`; a `304` means skip — cheap no-op. This is the primary lever
  for keeping the structure-fan-out affordable.
- **Error budget / 420:** ESI enforces an error-rate limit (the `X-ESI-Error-Limit-*`
  headers). Centralize handling in `providers.py`: on `420`/`5xx`, back off
  with jitter and let Celery retry (`autoretry_for`, `retry_backoff=True`,
  capped `max_retries`). Never tight-loop on errors.
- **Fan-out:** one structure = one observer call. Dispatch per-structure as
  independent Celery subtasks (a chord/group) so one slow/failing structure
  doesn't block the rest, and so retries are isolated.

---

## 7. Pricing Service (Janice)

RESEARCH.md specifies **refined values** via the **Janice API** — i.e. value
the *minerals an ore reprocesses into*, not the raw ore order price.

`core/pricing.py` exposes a single abstraction:

```python
class PriceProvider(Protocol):
    def refined_value(self, ore_type: EveType, quantity: int) -> Decimal: ...
```

**Resolved (confirmed against the Janice v2 OpenAPI spec):** Janice exposes
**no refined/reprocessed-ore valuation** — only market prices — so option 2
below is **not viable**. Whale Tax uses option 1 exclusively.

- Base URL: `https://janice.e-351.com/api/rest/v2` (`WHATAX_JANICE_BASE_URL`).
- Auth: header **`X-ApiKey`** (key stored on `TaxConfiguration`, never logged).
- Bulk price: **`POST /pricer?market=2`** (`2` = Jita), body `text/plain` =
  one type id per line. Response: array of items with `itemType.eid` and
  `immediatePrices` / `top5AveragePrices` → `{buyPrice, splitPrice, sellPrice,
  …}`. The `mineral_price_basis` config picks the (group, field) pair.

1. **Reprocess-then-price (the implementation, fully controlled).**
   `refined_value = Σ over materials m of (yield(ore, m) × mineral_price(m))`
   where `yield` comes from `EveTypeMaterial` (the SDE reprocessing output,
   normalized to the ore's reprocess batch size — ore reprocesses in fixed
   portions, commonly 100 units) and `mineral_price(m)` is a Janice market
   price for the mineral. This needs Janice only for a small, stable set of
   minerals (Tritanium…Megacyte + moon materials), which caches extremely well.

2. **Ask Janice for ore refined value directly**, if its appraisal API exposes a
   reprocessed/refined valuation mode for ore. This offloads the yield math but
   couples us to Janice's reprocessing-efficiency assumptions.

**Reprocessing efficiency** (station/structure rigs/skills) materially changes
refined value. It is a configurable factor on `TaxConfiguration.reprocessing_yield`
(**edited in the Admin tab**, default `0.9060`), applied uniformly and recorded
on each snapshot (`reprocessing_yield_applied`) so historical bills remain
reproducible. The mineral price basis (`TaxConfiguration.mineral_price_basis`) is
likewise Admin-tab config, recorded on the snapshot as `price_basis_applied`.

**Caching & resilience:**
- Cache mineral prices for a TTL (e.g. 1–6h) in Django cache; price drift
  within a month is acceptable and the monthly run is a single point-in-time.
- The Janice API key is configured in the **Admin tab** and stored on
  `TaxConfiguration.janice_api_key` ([§5.1](#51-configuration)). It is read once
  into the provider and **never rendered back** to the page or written to logs.
  (This supersedes the earlier "settings-only" stance — see the security note in
  [§5.1](#51-configuration) / [§16](#16-settings).)
- On Janice failure during a tax run: **do not** silently bill at zero. Fail the
  task, alert officers, and leave the period in `calculating` so it can be
  retried once pricing is back. (See "fail loud" in [§19](#19-risks--open-questions).)

> ✅ **Confirmed.** The base URL, `X-ApiKey` auth, and `POST /pricer` request /
> response shapes above were verified against the Janice v2 OpenAPI spec
> (`/api/rest/v2/swagger.json`). Implemented in `providers.py:JaniceClient` and
> `core/pricing.py:ReprocessPriceProvider`.

---

## 8. Mining Data Pipeline

```
[sync_structures] ──► MiningStructure rows
        │
[sync_mining_ledger] (per structure, fan-out) ──► MiningLedgerEntry (raw, upsert)
        │
[aggregate_period] ──► MiningSnapshot (per player/ore)
        │
[price_period] ──► MiningSnapshot.refined_value
```

> 🔌 Every box above reads and writes **Whale Tax-owned tables only** ([§5](#5-data-model)).
> The pipeline's sole external inputs are ESI (raw landing) and AA-core/eveuniverse
> for identity and ore metadata — there is no read path into `aa-moonmining` or any
> other peer app, so a re-sync or schema change here is entirely within our control.

**Player resolution (the crux).** Per RESEARCH.md, mining is attributed to the
**player** (AA `User`), and the **main character's corporation** decides the
rate — regardless of which alt mined.

```
ledger.character_id
   → EveCharacter (eveuniverse / AA)
   → CharacterOwnership.user            # the player
   → UserProfile.main_character          # main
   → main_character.corporation_id       # rate-determining corp
```

Edge cases that must be handled explicitly (don't let them silently drop ISK):

- **Unowned character** (mined by someone with no AA account / token gone):
  ownership lookup fails. Bucket into an `UNATTRIBUTED` pseudo-player and
  surface in the officer UI — never discard. *Implemented* as a lazily-created
  sentinel `auth.User` (`whatax_unattributed`, `is_active=False`, unusable
  password) via `core/aggregation.py:unattributed_user()`, so the non-null
  `PROTECT` FK on snapshots/records stays satisfied without a nullable column.
- **No main set:** fall back to default tax rate; flag the record.
- **Ownership changed mid-month:** resolve ownership *as of the run*, and freeze
  `corporation_at_calc` on the `TaxRecord` for audit.

**Aggregation** groups ledger rows for the period by resolved `user` + `ore_type`
and sums `quantity`. Because ledger rows are upserted on their natural key,
re-running aggregation is idempotent and safe to repeat (e.g. after a late ESI
sync or a retroactive correction).

---

## 9. Tax Calculation

**Schedule: emit on the 1st of each month for the *previous* month**, early (e.g.
00:30). **All Whale Tax times are EVE time (= UTC), always** — every period
boundary, `emitted_at`, `due_date`, and the edit window is computed in EVE time,
fixed in code, never the host's local tz and not a configurable knob. Billing on
the 1st (rather than chasing a "last day 23:59" cron) avoids the
last-day-of-month edge case and lets the ESI ledger settle before calc — see
[§13](#13-scheduled-tasks-celery-beat).

**Algorithm (`core/tax.py`):**

1. Ensure the previous month's ledger is fully synced (one final
   `sync_mining_ledger` pass before calc).
2. `aggregate_period` → snapshots; `price_period` → refined values.
   **Exclusion filter:** aggregation drops ledger rows whose `MiningStructure`
   is excluded — `is_active = False`, or the structure's sec class is excluded by
   `TaxConfiguration` ([§5.1](#51-configuration)). Excluded rows are still
   counted into a separate "excluded" bucket for the officer drilldown, never
   silently dropped.
3. For each player with (non-excluded) mining in the period:
   - `total = Σ snapshot.refined_value`
   - `rate, flat_discount = resolve_rate(player)`:
     `CorporationTaxRate` for the **main char's** corp if it exists (rate +
     per-corp flat ISK discount), else `TaxConfiguration.default_tax_rate`
     (no discount).
   - `gross = (total × rate).quantize(0.01, ROUND_HALF_UP)`
   - `flat_discount_applied = min(flat_discount, gross)` (a per-member discount,
     applied each month, never below 0); `charge = gross − flat_discount_applied`.
   - Upsert `TaxRecord(tax_period, user, …)` with `original_tax_due = charge`,
     `tax_due = charge`, `emitted_at = now`,
     `due_date = now + grace_period_days`. The bill opens as a **negative
     balance** of `charge` ([§5.3](#53-derived-snapshots--tax)).
4. Transition `TaxPeriod` → `finalized`; enqueue "tax due" notifications.

Calc is wrapped in a DB transaction per period and is **idempotent** — re-running
recomputes `total/rate` and refreshes `original_tax_due`, but **preserves
`amount_paid`, payment links, and any staff `TaxRecordEdit`** (a re-run does not
clobber a market correction: if an edit exists, `tax_due` keeps the corrected
value while `original_tax_due` re-tracks the computed one). `emitted_at`/`due_date`
are set once at first emission and not moved by re-runs.

**Rounding & currency:** all monetary math in `Decimal`, quantized to 2 dp with
`ROUND_HALF_UP` only at the final bill amount.

---

## 10. Payment Matching

```
[sync_wallet_journal] ──► WalletJournalEntry (raw, upsert on entry_id)
        │
[reconcile_payments] ──► Payment (+ TaxRecord.amount_paid / status)
```

**Source:** the journal of `TaxConfiguration.payment_corporation`, division
`payment_wallet_division`. Consider only **inflows** with relevant `ref_type`
(`player_donation`, and optionally `corporation_account_withdrawal` /
`player_trading` depending on how players are told to pay).

**Matching algorithm (`core/matching.py`):**

1. For each unprocessed inflow, resolve payer:
   `first_party_id → EveCharacter → CharacterOwnership.user`. Because matching
   keys on the **player** (via `CharacterOwnership`), a payment from **any** of
   the player's registered characters settles their bill — players don't have to
   pay from a specific alt, only *to* `payment_corporation`.
2. Apply the amount to that player's **outstanding** `TaxRecord`s, oldest period
   first, up to the amount paid (a single transfer may settle multiple months;
   an overpayment leaves a positive unallocated remainder on the `Payment`).
3. Update each touched `TaxRecord.amount_paid` and recompute `status` against the
   settled total `settled = amount_paid + Σ adjustments` ([§5.4](#54-payments)):
   - `settled >= tax_due` → `paid`
   - `0 < settled < tax_due` → `partial`
   - else unchanged (`pending`/`overdue`).
4. Mark the journal entry `is_processed = True`.

**Unmatched inflows** (payer has no AA account, or amount can't be attributed)
are stored as `Payment(match_method='unmatched', tax_record=NULL)` and listed in
the officer UI for manual assignment. **Never** auto-discard money.

**Reason-string hints:** if the corp asks players to put e.g. their main name or
a token in the transfer memo, use `reason` as a secondary signal when
`first_party_id` resolution is ambiguous — but treat it as a hint, not
authority.

**Idempotency:** keyed on `WalletJournalEntry.entry_id` + `is_processed`. A
re-run never double-credits.

---

## 11. Moon Status Tracking

### 11.1 Moon Pop (drilled / fractured)

Source of truth is **corp notifications**, not polling extraction times.
Relevant types from `GET /characters/{id}/notifications/`:

| Notification | Meaning |
|---|---|
| `MoonminingExtractionStarted` | Cycle scheduled → `MoonExtraction(status=scheduled/active)`. |
| `MoonminingLaserFired` | Manual fracture → **pop**. |
| `MoonminingAutomaticFracture` | Natural decay fracture → **pop**. |
| `MoonminingExtractionFinished` | Chunk arrived/ready. |
| `MoonminingExtractionCancelled` | Cancel → `status=cancelled`. |

On a pop event: set `status=popped`, `popped_at`, and (if `notified_pop_at` is
null) enqueue the moon-pop notification, then stamp `notified_pop_at`.

> The notification `text` is a YAML blob; parse defensively. On
> `MoonminingExtractionStarted`, persist `oreVolumeByType` into `ExtractionOre`
> rows and compute `total_good_ore_m3` — this is the dead-% denominator.
> (`aa-moonmining` is a useful reference for the exact YAML field shapes; we
> parse our own, see [§3](#3-prior-art--reuse).)

### 11.2 Moon Dead (≥95% good ore mined)

Computed in **volume (m³)**, since `oreVolumeByType` is volumetric and different
ores have different per-unit volumes:

`dead = mined_good_ore_m3 / total_good_ore_m3 ≥ WHATAX_DEAD_THRESHOLD (0.95)`,
counting **only** ores in the structure's effective good-ore set (the global
`GoodOreDefault` ± per-structure `StructureGoodOre` overrides — see [§5](#5-data-model)).

- **Denominator** (`total_good_ore_m3`): Σ of `ExtractionOre.volume_m3` for ores
  in the effective good-ore set, from the `MoonminingExtractionStarted`
  notification's `oreVolumeByType` snapshot ([§11.1](#111-moon-pop-drilled--fractured)).
  Recomputed live so good-ore config edits are retroactive. No moon-scan import,
  no chunk-volume estimation, no external app — the game tells us the exact
  composition. If the snapshot is missing or holds no good ore, the denominator
  is left NULL and dead-detection skips (never a fabricated `0`).
- **Numerator** (`mined_good_ore_m3`): for ledger rows on this structure for
  good ores **since `chunk_arrival_time`**, sum `quantity × EveType.volume`
  (per-unit m³ from `django-eveuniverse`). Recomputed by `update_moon_status`.

Caveat to keep honest: the observer ledger is daily/cumulative and ~30 days
deep, so the numerator tracks *mined* volume accurately but isn't real-time; the
`update_moon_status` cadence ([§13](#13-scheduled-tasks-celery-beat)) bounds how
stale "dead" can be. This is a freshness limit, not the old denominator-estimation
risk — that one is now resolved.

On crossing the threshold: set `status=dead`, `dead_at`; if `notified_dead_at`
is null, enqueue the moon-dead notification and stamp it.

---

## 12. Notifications

Dispatch lives in `notifications.py`; all sends are **idempotent** via the
`notified_*_at` stamps on the originating record (re-running a task never
re-pings).

| Event | Trigger | Channel(s) | Recipient |
|---|---|---|---|
| Tax due | period `finalized` | webhook + opt-in DM | player |
| Payment received | `reconcile_payments` match | webhook + opt-in DM | player |
| Moon pop | fracture notification | webhook | configured channel |
| Moon dead | ≥95% good ore | webhook | configured channel |

- **Webhook:** `dhooks-lite` to `TaxConfiguration.broadcast_webhook_url` (or a
  per-structure override if added later). Rich embeds: amount, period, due date,
  pay-to corp/wallet.
- **DM:** opt-in per player. Add a `PlayerNotificationPref` model
  (`user OneToOne`, `dm_opt_in Bool`) or reuse AA's notification settings.
  DMs require the AA Discord service to be configured; if absent, degrade
  gracefully to webhook-only and log.
- **Batching:** the end-of-month run can generate many "tax due" DMs at once —
  send them as individual Celery tasks (one per player) with rate-limit-aware
  retry so a Discord 429 on one doesn't stall the batch.

---

## 13. Scheduled Tasks (Celery Beat)

Documented for the operator to add to `local.py` (`CELERYBEAT_SCHEDULE`) or
manage via `django-celery-beat` admin.

| Task | Cadence | Purpose |
|---|---|---|
| `whatax.sync_structures` | daily | Refresh `MiningStructure`. |
| `whatax.sync_mining_ledger` | every 1–3h | Land observer ledger rows. |
| `whatax.sync_moon_extractions` | hourly | Extraction schedule. |
| `whatax.poll_corp_notifications` | every 15–30 min | Moon pop/fracture events. |
| `whatax.update_moon_status` | hourly | Recompute dead %. |
| `whatax.sync_wallet_journal` | every 30–60 min | Land wallet rows. |
| `whatax.reconcile_payments` | every 30–60 min | Match payments. |
| `whatax.run_monthly_tax` | **1st of month, ~00:30** | Emit previous month's bills + notify. |

**The monthly trigger — decided: 1st of the month for the previous month.**
Cron-clean (`0 30 0 1 * *`), no "last day of month" arithmetic, and it gives the
ESI ledger a few hours to settle past the month boundary before calc. This emits
each `TaxRecord` with `emitted_at = now` and `due_date = now + grace_period_days`
([§9](#9-tax-calculation)) — i.e. bills land on the 1st and the configurable
pay-by clock starts then. (A missed 1st is self-correcting: the task no-ops if the
previous month's period is already `finalized`, so a manual re-run any later day
still emits exactly once.)

All scheduled tasks must short-circuit when `TaxConfiguration.is_enabled` is
False, and acquire a lock (Django cache lock / Celery `singleton`) to prevent
overlapping runs of the same sync.

---

## 14. Permissions

Three **cumulative roles** — user ⊂ staff ⊂ admin — defined on an unmanaged
`General` model (the AA idiom):

```python
class General(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access",     "USER: own dashboard — frags, own mining, own tax record"),
            ("manage_payments",  "STAFF: fix payments, add/remove balances, view all records"),
            ("admin_access",     "ADMIN: configuration & dangerous actions (keys, rates, exclusions, calc)"),
        )
```

| Role | Permission(s) | Can do |
|---|---|---|
| **User** | `basic_access` | See current frags + upcoming frags (next 2 days); own mining last week / last month; own tax record — charges, balances, payments. Read-only, self-only. |
| **Staff** | `basic_access` + `manage_payments` | All of user, plus: view **all** players' records, fix tax payments, manually match/unmatch, **add or remove balances** (manual adjustments with audit note). Room to add more staff actions later. |
| **Admin** | + `admin_access` | All of staff, plus the **dangerous** surface: API keys, tax rates & per-corp overrides, payment corp/wallet, webhook, moon exclusions, good-ore, structure toggles, **run/re-run calc**, waive records, master enable switch. |

Roles are granted cumulatively via AA groups (a staff member's group carries both
`basic_access` and `manage_payments`); none of the higher permissions *imply* the
lower in code, so views check the specific permission they need.

- **Self vs. all:** querysets are filtered to `request.user` unless the caller
  has `manage_payments` or `admin_access`. Object-level scoping lives in
  `managers.py` (e.g. `TaxRecord.objects.visible_to(user)`), never inline in
  templates.
- **Balance adjustments** (staff) are not silent edits to `amount_paid`: model
  them as an auditable `BalanceAdjustment` row (who/when/amount/reason) that
  feeds into the record's balance, so money changes are always traceable.

---

## 15. Views & UI

Registered through AA hooks in `auth_hooks.py` (`MenuItemHook` gated on
`basic_access`, `UrlHook` at `^whatax/`). Icon e.g. `fas fa-coins`.

**The UI is a tabbed single app**, gated by the three roles in
[§14](#14-permissions): a **user** sees only **Dashboard**; **staff** also sees
**Staff**; **admin** also sees **Admin**. The Admin tab is the only place config
lives — nothing routes the operator to the Django admin.

### 15.1 Dashboard tab — `basic_access` (user)

A deliberately simple, self-only view with three panels:

- **Frags** — *current* frags (recently popped / active extractions) and
  *upcoming* frags within the next **2 days** (`chunk_arrival_time` /
  `natural_decay_time` ≤ now + 48h) across tracked structures
  ([§11](#11-moon-status-tracking)). Read-only situational awareness for members.
- **My mining** — the logged-in player's mined amount for **last week** and
  **last month** (rolling windows over `MiningLedgerEntry` for the user's
  characters; shown as quantity and refined value). Distinct from the monthly
  *tax period* — this is a quick "how much have I pulled" gauge.
- **My tax record** — current + historical `TaxRecord`s: **charges** (`tax_due`),
  **balance** (signed, `amount_paid + Σ adjustments − tax_due`; negative = owed),
  **due date**, and **payments** (matched payment history + any
  `BalanceAdjustment`s). Status badge and pay-to info (corp + wallet + memo
  instructions). No knobs.

### 15.2 Staff tab — `manage_payments`

Review + payment-correction surface across **all** players for a period: all
`TaxRecord`s, the `UNATTRIBUTED` mining bucket, the **excluded-mining** bucket
([§9](#9-tax-calculation)), unmatched payments, and per-corp totals. Drills into
a per-player / per-ore breakdown. **Staff actions:**

- Manually match / unmatch a payment.
- **Add or remove a balance** (creates a `BalanceAdjustment`, [§5.4](#54-payments),
  required reason).
- **Edit a bill's tax amount** (`tax_due`) to correct for ore-price market
  manipulation — **only within 15 days of `emitted_at`** (the
  `tax_edit_window_days` window); each edit logs a `TaxRecordEdit` and leaves
  `original_tax_due` intact. The action is hidden *and* server-refused once the
  window closes.

Deliberately *not* here: rate/key config, exclusions, or running calc — those are
admin-only. (More staff actions can be added later without touching the admin
surface.)

### 15.3 Admin tab — `admin_access` (the dangerous configuration surface)

Everything that used to imply "go into Django admin" lives here, as sub-sections
of one tab, all writing the config models in [§5.1](#51-configuration):

| Sub-section | Edits | Backing |
|---|---|---|
| **API keys** | Janice API key (write-only field; shows *set / not set*), payment corp + wallet division, broadcast webhook. | `TaxConfiguration` |
| **General tax** | Default / general tax rate, grace period, master enable switch. | `TaxConfiguration` |
| **Pricing** | Reprocessing yield factor; Janice mineral price basis (split/buy/sell × immediate/top5). | `TaxConfiguration` |
| **Corp overrides** | Add / edit / remove a per-corporation override (corp picker + rate + optional flat ISK discount + note). | `CorporationTaxRate` |
| **Moon exclusions** | Toggle exclude HS / LS / NS; per-structure include/exclude toggles (with each structure's resolved sec class shown). | `TaxConfiguration` + `MiningStructure.is_active` |
| **Good ore** | Global default good-ore set + per-structure overrides. | `GoodOreDefault`, `StructureGoodOre` |
| **Calc & records** | Run / re-run a period's calc; **waive** a `TaxRecord` entirely (forgive the bill, audit note). Amount *edits* are a staff action ([§15.2](#152-staff-tab--manage_payments)). | tasks + `TaxRecord` |

### 15.4 Routing

| View | Path | Perm | Tab |
|---|---|---|---|
| Dashboard | `whatax:index` | `basic_access` | Dashboard |
| Staff | `whatax:staff` | `manage_payments` | Staff |
| Period detail | `whatax:period/<id>` | `manage_payments` | Staff |
| Manual match | `whatax:payment/<id>/match` | `manage_payments` | Staff |
| Balance adjust | `whatax:record/<id>/adjust` | `manage_payments` | Staff |
| Edit tax (≤15d) | `whatax:record/<id>/edit-tax` | `manage_payments` | Staff |
| Record waive | `whatax:record/<id>/waive` | `admin_access` | Admin |
| Admin / config | `whatax:admin` | `admin_access` | Admin |

Templates extend AA's base (`allianceauth/base.html` family); the tab strip is a
shared partial that hides tabs the user can't access. Heavy tables use DataTables
(bundled with AA) with server-side pagination for large periods.

---

## 16. Settings

**Config-in-app, not settings.** Operator-facing configuration — API key, tax
rates, payment corp/wallet, webhook, exclusions, good-ore — is set in the **Admin
tab** ([§15.3](#153-admin-tab--admin_access-the-dangerous-configuration-surface)) and
stored on the config models ([§5.1](#51-configuration)). `local.py` carries only
a handful of pure-operational, deploy-time knobs that have no business in a UI.

`app_settings.py` reads these from Django settings with safe defaults:

```python
WHATAX_JANICE_BASE_URL       = "https://janice.e-351.com/api/rest/v2"
WHATAX_JANICE_MARKET_ID      = 2         # 2 = Jita
WHATAX_PRICE_CACHE_TTL       = 3600      # seconds
WHATAX_LEDGER_LOOKBACK_DAYS  = 30        # ESI observer depth
WHATAX_DEAD_THRESHOLD        = 0.95      # good-ore fraction for "dead"

# Seed defaults only — the live values are TaxConfiguration fields (Admin tab):
WHATAX_REPROCESSING_YIELD_DEFAULT   = 0.906
WHATAX_MINERAL_PRICE_BASIS_DEFAULT  = "split_immediate"
```

> **Time is always EVE time (UTC) — not a setting.** Whale Tax never reads a
> timezone from settings or the host; all period boundaries, emission, due dates,
> and the edit window are computed in EVE time, fixed in code. (There is
> deliberately no `WHATAX_TIMEZONE`.)
>
> The **Janice API key is no longer a setting** either — it moved to the Admin
> tab / `TaxConfiguration.janice_api_key`. This trades the env-secret guarantee
> for a single config surface; see the security note in [§5.1](#51-configuration).
> The **reprocessing yield** and the **mineral price basis** likewise moved to
> `TaxConfiguration` (Admin tab); the `*_DEFAULT` settings above only seed a new
> config row. Sec banding for exclusions is fixed in code (`core/moons.py`), not
> a setting, so calc and UI can't drift.

---

## 17. Installation

Operator-facing (for README, summarized here):

1. `pip install aa-whatax`
2. Add `"whatax"` to `INSTALLED_APPS` in `local.py`.
3. Ensure `django-eveuniverse` and `django-esi` are installed/configured (AA
   standard).
4. Optionally set the operational knobs in [§16](#16-settings) via `local.py`
   (all have defaults; the Janice **key** is *not* here — it's set in-app at
   step 10).
5. `python manage.py migrate`
6. `python manage.py collectstatic`
7. Add the Whale Tax tasks to `CELERYBEAT_SCHEDULE` (see [§13](#13-scheduled-tasks-celery-beat)).
8. Restart AA (Gunicorn + Celery worker + beat).
9. Add ESI tokens: a director-role char for structures/extractions, an
   accountant-role char for wallet, per [§6.1](#61-required-scopes).
10. Open the app's **Admin tab** ([§15.3](#153-admin-tab--admin_access-the-dangerous-configuration-surface))
    and set: Janice API key, default rate + per-corp overrides, payment
    corp/wallet, webhook, moon exclusions (HS/LS/NS + per-structure), and
    per-structure good ores. (No Django-admin step.)
11. Preload static data so pricing has yields available. First set
    `EVEUNIVERSE_LOAD_TYPE_MATERIALS = True` in `local.py` (otherwise no
    `EveTypeMaterial` reprocessing-yield rows are created), then run
    `python manage.py eveuniverse_load_types whatax --category_id 25` (category 25
    = Asteroid, i.e. all ore types incl. moon ores; loading with materials enabled
    also pulls in the referenced minerals/moon materials). The bare `whatax` arg
    is only an app label — without an ID flag the command reports "No IDs
    specified. Nothing to do." and loads nothing.

---

## 18. Testing Strategy

- **Unit tests** for everything in `core/` with ESI/Janice mocked at
  `providers.py`. Pricing, aggregation, rate resolution, and matching are pure
  functions over fixtures — cover them heavily, including the edge cases in
  [§8](#8-mining-data-pipeline)/[§10](#10-payment-matching).
- **Factory fixtures** for AA objects (`EveCharacter`, `CharacterOwnership`,
  `UserProfile`) — reuse `allianceauth.tests` / `app-utils` helpers; do not hit
  real ESI in tests.
- **Idempotency tests:** run each sync/aggregate/calc/reconcile twice; assert no
  duplicate rows, no double-credit, no double-notify.
- **Money tests:** assert `Decimal` throughout and correct `ROUND_HALF_UP`
  quantization; a regression here is a real-ISK bug.
- **Determinism:** freeze time (`freezegun`) for period boundaries, the
  1st-of-month emission, the `due_date` / `overdue` transition, and the 15-day
  `tax_edit_window_days` guard (test exactly on, just inside, and just outside
  the window).

---

## 19. Risks & Open Questions

Carried forward from RESEARCH.md, with technical positions:

1. **`aa-moonmining` — resolved: not a dependency.** Whale Tax is self-contained
   for flexibility ([§3](#3-prior-art--reuse)); `aa-moonmining` is reference-only.
2. **Moon-dead denominator — resolved.** `total_good_ore_m3` comes from the
   `MoonminingExtractionStarted` notification's `oreVolumeByType`, not from
   estimation ([§11.2](#112-moon-dead-95-good-ore-mined)). The *residual* risk is
   **notification coverage**: if the corp-notification poll misses the
   extraction-started event (ESI returns only the most recent notifications, with
   limited retention), an extraction has no composition and thus no denominator.
   Mitigation: poll frequently ([§13](#13-scheduled-tasks-celery-beat)); for
   extractions first seen via the `extractions` endpoint without a matching
   started-notification, mark `total_good_ore_m3 = NULL` and skip dead-detection
   for that cycle (don't fabricate a denominator), surfacing it as "composition
   unknown" in the UI.
3. **ESI ledger depth (~30 days) & granularity.** The observer ledger is daily
   and shallow. Sync **frequently** (hourly-ish) so no day is lost; never rely on
   one end-of-month pull. Upsert-on-natural-key makes frequent syncs safe.
4. **Retroactive corrections.** Because raw tables are the source of truth and
   all derived stages are idempotent, a correction = re-sync raw (if still in
   ESI window) → re-aggregate → re-calc. For data older than the ESI window,
   correction must be a manual officer override on the `TaxRecord`.
5. **Pricing failure handling.** Fail loud: never bill at zero on a Janice
   outage. Leave period in `calculating`, alert officers, retry.
6. **Janice API specifics — resolved.** Confirmed against the v2 OpenAPI spec:
   base `https://janice.e-351.com/api/rest/v2`, `X-ApiKey` header, bulk
   `POST /pricer?market=2`. Janice has **no refined-ore mode**, so refined value
   is always computed via reprocess-then-price ([§7](#7-pricing-service-janice)).
7. **Reprocessing efficiency assumption.** Refined value depends on yield;
   make it configurable and record the factor on each snapshot for
   reproducibility.
8. **In-game role vs. scope.** A token can have the scope but lack the corp role
   (→403). Detect and surface this distinctly in the UI.
9. **Unattributed mining & unmatched payments.** Both must be visible and
   manually resolvable — money is never silently created or destroyed.
10. **Timezone correctness — resolved.** Whale Tax **always** uses EVE time
    (= UTC): every period boundary, the 1st-of-month emission, `due_date`, the
    `overdue` transition, and the 15-day edit window are computed in EVE time,
    fixed in code — never host-local, not a configurable setting.

