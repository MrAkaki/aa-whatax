# aa-whatax

Moon-mining **tax & accounting** plugin for [Alliance Auth](https://gitlab.com/allianceauth/allianceauth).

Whale Tax pulls corp mining-observer ledgers, extraction schedules, and wallet
journals from ESI; values mined ore at its **refined** (reprocessed-mineral)
worth via Janice; bills each **player** (not each character) at a per-corp or
default rate; reconciles wallet donations against those bills; and tracks moon
**pop** / **dead** status from corp notifications — broadcasting all of it to
Discord.

Everything is configured from the plugin's own **tabbed site UI** (not the Django
admin), gated by three roles:

- **User** — a simple **Dashboard**: current + upcoming (2-day) frags, own mining
  last week / month, and own tax record (charges, balances, payments).
- **Staff** — a **Staff** tab to fix payments and add/remove balances across all
  players.
- **Admin** — an **Admin** tab for the dangerous settings: Janice API key, general
  tax rate + per-corp overrides, moon exclusions (security class HS/LS/NS or per
  structure), good ore, and running calc.

See [RESEARCH.md](RESEARCH.md) for the what/why and [TECHNICAL.md](TECHNICAL.md)
for the full implementation specification.

> **Status:** initial full vertical implemented — data model + migration, ESI
> sync tasks, Janice pricing, tax calc, payment matching, moon tracking, the
> tabbed UI, and unit tests are all in place per TECHNICAL.md. Run
> `python manage.py makemigrations whatax --check` in your AA env to validate the
> hand-authored initial migration, then `migrate` and `test whatax`.

## Installation

> Operator steps (full detail in [TECHNICAL.md §17](TECHNICAL.md)):

1. `pip install git+https://github.com/MrAkaki/aa-whatax.git@main`
2. Add `"whatax"` to `INSTALLED_APPS` in `local.py`.
3. Ensure `django-eveuniverse` and `django-esi` are installed/configured.
4. Optionally set the operational knobs (TECHNICAL.md §16) via `local.py` — all
   have defaults; the Janice **key** is set in-app, not here.
5. `python manage.py migrate`
6. `python manage.py collectstatic`
7. Add the Whale Tax tasks to `CELERYBEAT_SCHEDULE` (TECHNICAL.md §13).
8. Restart AA (Gunicorn + Celery worker + beat).
9. Add ESI tokens (director-role char for structures/extractions, accountant-role
   char for wallet — TECHNICAL.md §6.1).
10. Open the app's **Admin tab** and set the Janice key, default + per-corp tax
    rates, the reprocessing yield + mineral price basis, payment corp/wallet,
    webhook, moon exclusions, and the good-ore set — global default + per-structure
    overrides (TECHNICAL.md §15.3).
11. Set `EVEUNIVERSE_LOAD_TYPE_MATERIALS = True` in `local.py` (required so
    reprocessing yields are loaded), then preload ores + reprocessing materials:
    `python manage.py eveuniverse_load_types whatax --category_id 25` (category 25
    = Asteroid). The bare `whatax` arg is only a label — without `--category_id`
    the command loads nothing ("No IDs specified").
12. Seed the global good-ore set for moon dead-detection:
    `python manage.py whatax_seed_good_ores` (adds all moon ore types). Add
    `--recompute` to re-evaluate existing extractions against the seeded set
    (backfilled dead transitions are silent; add `--notify` to broadcast them).
    Tune the list afterwards in **Admin tab → Manage good ore**.

## Celery tasks

Whale Tax does all its ESI sync, payment matching, and monthly billing from
Celery Beat. Add the tasks below to `CELERYBEAT_SCHEDULE` in `local.py` (or
register them via the `django-celery-beat` admin). All tasks short-circuit while
the **Admin tab → "enabled"** kill-switch is off, and each holds a lock so runs
never overlap — so it's safe to schedule them generously.

| Task | Recommended interval | Purpose |
|---|---|---|
| `whatax.tasks.sync_structures` | daily | Refresh the corp's mining structures. |
| `whatax.tasks.sync_mining_ledger` | every 1–3 h | Land mining-observer ledger rows. |
| `whatax.tasks.sync_moon_extractions` | hourly | Refresh the moon extraction schedule. |
| `whatax.tasks.poll_corp_notifications` | every 15–30 min | Catch moon pop / fracture events. |
| `whatax.tasks.update_moon_status` | hourly | Recompute moon "dead" percentage. |
| `whatax.tasks.sync_wallet_journal` | every 30–60 min | Land payment-wallet journal rows. |
| `whatax.tasks.reconcile_payments` | every 30–60 min | Match wallet inflows to bills. |
| `whatax.tasks.run_monthly_tax` | **1st of month, 00:30** | Emit the previous month's bills + notify. |

> Schedule `reconcile_payments` to run shortly **after** `sync_wallet_journal`
> so freshly-landed inflows get matched in the same cycle. Do **not** schedule
> `whatax.tasks.sync_structure_ledger` — `sync_mining_ledger` fans it out per structure.

Example for `local.py`:

```python
from celery.schedules import crontab

CELERYBEAT_SCHEDULE.update({
    "whatax_sync_structures": {
        "task": "whatax.tasks.sync_structures",
        "schedule": crontab(minute=5, hour=0),            # daily, 00:05
    },
    "whatax_sync_mining_ledger": {
        "task": "whatax.tasks.sync_mining_ledger",
        "schedule": crontab(minute=0, hour="*/2"),        # every 2 h
    },
    "whatax_sync_moon_extractions": {
        "task": "whatax.tasks.sync_moon_extractions",
        "schedule": crontab(minute=10),                   # hourly
    },
    "whatax_poll_corp_notifications": {
        "task": "whatax.tasks.poll_corp_notifications",
        "schedule": crontab(minute="*/15"),               # every 15 min
    },
    "whatax_update_moon_status": {
        "task": "whatax.tasks.update_moon_status",
        "schedule": crontab(minute=20),                   # hourly
    },
    "whatax_sync_wallet_journal": {
        "task": "whatax.tasks.sync_wallet_journal",
        "schedule": crontab(minute="0,30"),               # every 30 min
    },
    "whatax_reconcile_payments": {
        "task": "whatax.tasks.reconcile_payments",
        "schedule": crontab(minute="10,40"),              # 30 min, just after journal sync
    },
    "whatax_run_monthly_tax": {
        "task": "whatax.tasks.run_monthly_tax",
        "schedule": crontab(minute=30, hour=0, day_of_month=1),  # 1st of month, 00:30
    },
})
```

The monthly run bills the **previous** month and is self-correcting: it no-ops if
that period is already finalized, so a missed 1st can be re-run manually any later
day and still emits exactly once. See
[TECHNICAL.md §13](TECHNICAL.md#13-scheduled-tasks-celery-beat) for the rationale.

## License

GPL-3.0-or-later.
