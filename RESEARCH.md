# Whale Tax — Research & Design Document

## Overview

A Django plugin for **Alliance Auth** that manages moon mining taxes for a corporation's mining league. All moon structures belong to the corporation. The plugin tracks what each player mined, calculates their tax liability, records payments, and sends notifications.

---

## Core Concepts

### Player vs Character
- A **player** = one Alliance Auth user account.
- A player may have **many characters** (alts).
- Character ownership is resolved via Alliance Auth's existing character ownership system.
- The **main character** determines which corporation's tax rate applies to **all** of that player's mining — regardless of which character actually did the mining.

### Tax Rates
- A **default tax rate** applies globally.
- **Per-corporation exceptions** override the default for players whose main character belongs to that corporation.
- Only exceptions need to be configured; everything else falls back to the default.

### Tax Calculation Schedule
- Runs on the **last day of each month at 23:59**.
- Covers all mining done within that calendar month.

---

## Mining Data Pipeline

1. Pull the **mining ledger** from all corporation-owned structures via ESI.
2. Each ledger entry contains: character, ore type ID, quantity, structure.
3. **Aggregate** all entries:
   - Group by player (resolved from character → Auth user → main character's corporation).
   - Sum quantities per ore type across all structures and all characters belonging to that player.
4. This gives a per-player, per-ore breakdown for the month.

### Pricing
- Ore value is calculated using the **Janice API** — specifically **refined values** (not raw buy price or split price).
- Tax amount = total refined value of player's mined ore × their applicable tax rate.

---

## Payment Tracking

- All tax records stored in the database.
- Payment is tracked by monitoring the wallet of a **designated alt corporation**.
- Incoming donations/transfers from player characters are **matched** to outstanding tax liabilities.
- Notifications are sent to players to prompt payment (see Notifications section).

---

## Moon Status Tracking

### Good Ores
- Not all ores in a moon are worth mining; only a subset are considered **"good ores"**.
- Good ores are configured **per structure** (not globally or per moon).

### Moon Dead Detection
- A moon is considered **dead** (fully mined) when **≥ 95%** of the good ore for that structure has been mined.
- Ores not in the "good ores" list for a structure are **ignored** for this calculation.

### Moon Pop Detection
- Detected from ESI when the moon chunk is **drilled / exploded**.
- This is an ESI-driven event, not manually logged.

---

## Notifications

### Channels
- **Discord webhook** — broadcast to a configured channel (corp-wide or officer channel).
- **Discord DM** — opt-in per player for direct messages.

### Notification Types

| Event | Trigger | Recipients |
|---|---|---|
| Tax due | End-of-month calculation run | Player (webhook + opt-in DM) |
| Payment received / matched | Wallet match | Player (webhook + opt-in DM) |
| Moon pop | ESI drill/explosion event | Configured webhook |
| Moon dead | ≥ 95% good ore mined | Configured webhook |

---

## Data Model (Preliminary)

### Config / Setup
- `TaxConfig` — default tax rate, alt corp wallet reference
- `CorporationTaxRate` — per-corporation override rate
- `StructureConfig` — per-structure list of good ore type IDs

### Monthly Cycle
- `MiningSnapshot` — raw aggregated mining per player per ore per month
- `TaxRecord` — calculated tax liability per player per month (amount, rate applied, status)
- `Payment` — matched wallet donation (character, amount, date, linked TaxRecord)

### Moon Tracking
- `MoonCycle` — tracks a moon's active extraction cycle (pop time, dead flag, % good ore mined)

---

## External Dependencies

| Dependency | Purpose |
|---|---|
| Alliance Auth | User/character management, SSO, permissions |
| ESI (EVE Swagger Interface) | Mining ledger, extraction events |
| Janice API | Refined ore pricing |
| Discord Webhooks | Notifications |

---

## Open Questions / Future Scope

- Pagination / rate limiting strategy for ESI mining ledger calls across many structures.
- How far back ESI mining ledger data is available (ESI returns ~30 days).
- Handling retroactive corrections if ESI data was unavailable at calculation time.
- UI for officers to review, adjust, or override individual tax records.
- UI for players to view their own tax history and payment status.
