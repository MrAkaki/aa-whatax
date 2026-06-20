"""Notification dispatch: broadcast webhook + opt-in Discord DM, idempotent per record."""

import datetime as dt
import logging

from whatax.core.config import get_config
from whatax.core.timeutils import eve_now

logger = logging.getLogger(__name__)


def _send_webhook(message: str = "", *, embed=None) -> bool:
    """Send to the configured broadcast webhook. Returns True on dispatch."""
    config = get_config()
    url = config.broadcast_webhook_url
    if not url:
        logger.warning("whatax: no broadcast_webhook_url configured; skipping webhook")
        return False
    try:
        from dhooks_lite import Webhook

        hook = Webhook(url)
        hook.execute(content=message, embeds=[embed] if embed else None)
        return True
    except Exception:  # noqa: BLE001 - never let a notification break a task
        logger.exception("whatax: webhook dispatch failed")
        return False


def _send_dm(user, message: str) -> bool:
    """Opt-in Discord DM; degrades to no-op if the service/opt-in is absent."""
    from whatax.models import PlayerNotificationPref

    pref = PlayerNotificationPref.objects.filter(user=user, dm_opt_in=True).first()
    if pref is None:
        return False
    try:
        from allianceauth.services.modules.discord.models import DiscordUser

        discord_user = DiscordUser.objects.get(user=user)
        discord_user.send_message(content=message)
        return True
    except Exception:  # noqa: BLE001 - DM is best-effort; webhook is the guarantee
        logger.info("whatax: DM to user %s not delivered (service/opt-in unavailable)", user)
        return False


def _staff_recipients():
    """Users holding the structures-view or payments-staff permission (incl. groups/superusers)."""
    from django.contrib.auth.models import Permission, User
    from app_utils.django import users_with_permission

    perms = Permission.objects.filter(
        content_type__app_label="whatax",
        codename__in=["view_structures", "manage_payments"],
    )
    users = User.objects.none()
    for perm in perms:
        users = users | users_with_permission(perm)
    return users.distinct()


def _dm_staff(message: str) -> bool:
    """DM every staff member / structure-viewer via aadiscordbot; no-op if it's unavailable."""
    try:
        from aadiscordbot.tasks import send_message
    except Exception:  # noqa: BLE001 - aadiscordbot is optional
        logger.info("whatax: aadiscordbot unavailable; skipping staff DM")
        return False
    sent = False
    for user in _staff_recipients():
        try:
            # send_message resolves the user's Discord link and enqueues to the bot.
            send_message(user=user, message=message)
            sent = True
        except Exception:  # noqa: BLE001 - one bad recipient mustn't break the rest
            logger.info("whatax: staff DM to %s not delivered", user)
    return sent


def _embed(title, description, *, color=0x1F8B4C, fields=None):
    from dhooks_lite import Embed

    embed = Embed(title=title, description=description, color=color)
    for name, value in (fields or []):
        embed.add_field(name=name, value=str(value), inline=True)
    return embed


def _dashboard_url() -> str:
    """Absolute URL to the user dashboard, falling back to the relative path."""
    from django.conf import settings
    from django.urls import reverse

    rel = reverse("whatax:index")
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}{rel}" if base else rel


def _ore_amounts(extraction) -> str:
    """Markdown bullet list of the chunk's ore composition, falling back to the prior chunk."""
    ores = list(extraction.ores.select_related("ore_type").order_by("-volume_m3"))
    if ores:
        return "\n".join(f"• {o.ore_type}: {o.volume_m3:,.0f} m³" for o in ores)

    from whatax.models import ExtractionOre, MoonExtraction

    prior = (
        MoonExtraction.objects.filter(structure=extraction.structure, ores__isnull=False)
        .exclude(pk=extraction.pk)
        .order_by("-chunk_arrival_time")
        .first()
    )
    if prior is None:
        return "_composition unknown_"
    fallback = ExtractionOre.objects.filter(extraction=prior).select_related(
        "ore_type"
    ).order_by("-volume_m3")
    lines = "\n".join(f"• {o.ore_type}: {o.volume_m3:,.0f} m³" for o in fallback)
    return f"_approximate — from the previous chunk:_\n{lines}"


# --- Events -----------------------------------------------------------------


def notify_tax_due(record) -> bool:
    """Webhook + opt-in DM that an invoice was emitted (idempotent on notified_due_at)."""
    if record.notified_due_at is not None:
        return False
    config = get_config()
    pay_to = config.payment_corporation
    url = _dashboard_url()
    due = f"{record.due_date:%Y-%m-%d}" if record.due_date else "—"
    msg = (
        f"Tax invoice for **{record.user}** — {record.tax_period}: "
        f"{record.tax_due:,.2f} ISK (due {due}).\n\n"
        f"Review your invoice on the [dashboard]({url})."
    )
    embed = _embed(
        "Whale Tax — Tax Invoice",
        msg,
        color=0xE67E22,
        fields=[
            ("Period", record.tax_period),
            ("Amount", f"{record.tax_due:,.2f} ISK"),
            ("Due", due),
            ("Pay to", pay_to or "(unconfigured)"),
        ],
    )
    _send_webhook(embed=embed)
    _send_dm(record.user, msg)
    record.notified_due_at = eve_now()
    record.save(update_fields=["notified_due_at"])
    return True


def notify_payment_received(payment) -> bool:
    """DM-only notice that a payment was matched (idempotent on notified_at)."""
    if payment.notified_at is not None or payment.user is None:
        return False
    msg = f"Payment received from **{payment.user}**: {payment.amount:,.2f} ISK."
    _send_dm(payment.user, msg)
    payment.notified_at = eve_now()
    payment.save(update_fields=["notified_at"])
    return True


def notify_moon_pop(extraction) -> bool:
    """Webhook that a moon chunk was fractured (idempotent on notified_pop_at)."""
    if extraction.notified_pop_at is not None:
        return False
    description = (
        f"**{extraction.structure}** fractured — chunk ready to mine.\n\n"
        f"**Ore amounts:**\n{_ore_amounts(extraction)}"
    )
    _send_webhook(embed=_embed("Whale Tax — Moon Pop", description, color=0x9B59B6))
    extraction.notified_pop_at = eve_now()
    extraction.save(update_fields=["notified_pop_at"])
    return True


# Low-fuel reminder cadence: daily under the warning threshold, escalating to
# every 6h once a structure is at/below the critical threshold.
_FUEL_REMINDER_DAILY = dt.timedelta(hours=24)
_FUEL_REMINDER_CRITICAL = dt.timedelta(hours=6)


def reconcile_low_fuel(structure, config) -> bool:
    """Send a low-fuel DM on the right cadence, or re-arm the structure on refuel."""
    days = structure.fuel_days_left
    if days is None or days >= config.fuel_warning_days:
        # Refueled or fuel reading lost: clear so the next drain re-alerts.
        if structure.notified_low_fuel_at is not None:
            structure.notified_low_fuel_at = None
            structure.save(update_fields=["notified_low_fuel_at"])
        return False
    interval = (
        _FUEL_REMINDER_CRITICAL if days <= config.fuel_critical_days else _FUEL_REMINDER_DAILY
    )
    last = structure.notified_low_fuel_at
    if last is not None and eve_now() - last < interval:
        return False
    return notify_structure_low_fuel(structure)


def notify_structure_low_fuel(structure) -> bool:
    """DM staff that a structure is low on fuel and stamp the reminder time.

    Sends unconditionally — the cadence (daily, or 6-hourly when critical) is
    decided by ``reconcile_low_fuel``; this stamps ``notified_low_fuel_at`` so the
    next reminder is spaced correctly.
    """
    days = structure.fuel_days_left
    expires = f"{structure.fuel_expires:%Y-%m-%d %H:%M} EVE" if structure.fuel_expires else "—"
    system = structure.eve_solar_system or "—"
    message = (
        f"⛽ **Low fuel** — **{structure}** ({system}) has **{days} day(s)** of fuel "
        f"left (runs out {expires}). Top it off before the drill goes offline."
    )
    _dm_staff(message)
    structure.notified_low_fuel_at = eve_now()
    structure.save(update_fields=["notified_low_fuel_at"])
    return True


def reconcile_pop_drift(structure) -> bool:
    """DM staff once when a structure's next pop drifts off schedule; clear when realigned."""
    if not structure.is_off_schedule():
        if structure.notified_drift_at is not None:
            structure.notified_drift_at = None
            structure.save(update_fields=["notified_drift_at"])
        return False
    if structure.notified_drift_at is not None:
        return False  # already alerted for this drift episode
    next_pop = structure.next_scheduled_pop()
    planned = structure.planned_pop_at
    system = structure.eve_solar_system or "—"
    message = (
        f"⚠️ **Off-schedule pop** — **{structure}** ({system}) — a new pop is set for "
        f"**{next_pop:%Y-%m-%d %H:%M} EVE**, off the planned **{planned:%Y-%m-%d} EVE** "
        f"schedule. Review it on the structures page and accept the new cadence if intended."
    )
    _dm_staff(message)
    structure.notified_drift_at = eve_now()
    structure.save(update_fields=["notified_drift_at"])
    return True


def notify_moon_dead(extraction) -> bool:
    """Webhook that a moon hit the dead threshold (idempotent on notified_dead_at)."""
    if extraction.notified_dead_at is not None:
        return False
    description = (
        f"**{extraction.structure}** has hit the dead threshold — the good ore is "
        "mined out.\n\nThere may be some left-overs, but you don't need to mine "
        "them unless you're already on grid."
    )
    _send_webhook(embed=_embed("Whale Tax — Moon Dead", description, color=0xC0392B))
    extraction.notified_dead_at = eve_now()
    extraction.save(update_fields=["notified_dead_at"])
    return True
