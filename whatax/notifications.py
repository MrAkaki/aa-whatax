"""Notification dispatch (TECHNICAL.md §12).

Webhook (dhooks-lite) + opt-in Discord DM. Every send is idempotent via the
``notified_*_at`` stamp on the originating record — re-running a task never
re-pings. End-of-month "tax due" fan-out is sent as one Celery task per player
with rate-limit-aware retry so a single Discord 429 can't stall the batch.

Events: tax due (period finalized), payment received (reconcile match),
moon pop (fracture notification), moon dead (>=95% good ore).
"""

import logging

from whatax.core.timeutils import eve_now

logger = logging.getLogger(__name__)


def _config():
    from whatax.models import TaxConfiguration

    return TaxConfiguration.objects.get_solo()


def _send_webhook(message: str = "", *, embed=None) -> bool:
    """Send to the configured broadcast webhook. Returns True on dispatch."""
    config = _config()
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


def _embed(title, description, *, color=0x1F8B4C, fields=None):
    from dhooks_lite import Embed

    embed = Embed(title=title, description=description, color=color)
    for name, value in (fields or []):
        embed.add_field(name=name, value=str(value), inline=True)
    return embed


# --- Events -----------------------------------------------------------------


def notify_tax_due(record) -> bool:
    """Webhook + opt-in DM that a bill was emitted (idempotent on notified_due_at)."""
    if record.notified_due_at is not None:
        return False
    config = _config()
    pay_to = config.payment_corporation
    msg = (
        f"Tax due for **{record.user}** — {record.tax_period}: "
        f"{record.tax_due:,.2f} ISK (due {record.due_date:%Y-%m-%d})."
    )
    embed = _embed(
        "Whale Tax — Tax Due",
        msg,
        color=0xE67E22,
        fields=[
            ("Period", record.tax_period),
            ("Amount", f"{record.tax_due:,.2f} ISK"),
            ("Due", f"{record.due_date:%Y-%m-%d}" if record.due_date else "—"),
            ("Pay to", pay_to or "(unconfigured)"),
        ],
    )
    _send_webhook(embed=embed)
    _send_dm(record.user, msg)
    record.notified_due_at = eve_now()
    record.save(update_fields=["notified_due_at"])
    return True


def notify_payment_received(payment) -> bool:
    """Webhook + opt-in DM that a payment was matched (idempotent on notified_at)."""
    if payment.notified_at is not None or payment.user is None:
        return False
    msg = f"Payment received from **{payment.user}**: {payment.amount:,.2f} ISK."
    _send_webhook(embed=_embed("Whale Tax — Payment Received", msg))
    _send_dm(payment.user, msg)
    payment.notified_at = eve_now()
    payment.save(update_fields=["notified_at"])
    return True


def notify_moon_pop(extraction) -> bool:
    """Webhook that a moon chunk was fractured (idempotent on notified_pop_at)."""
    if extraction.notified_pop_at is not None:
        return False
    msg = f"Moon **popped**: {extraction.eve_moon or extraction.structure} fractured."
    _send_webhook(embed=_embed("Whale Tax — Moon Pop", msg, color=0x9B59B6))
    extraction.notified_pop_at = eve_now()
    extraction.save(update_fields=["notified_pop_at"])
    return True


def notify_moon_dead(extraction) -> bool:
    """Webhook that a moon hit the dead threshold (idempotent on notified_dead_at)."""
    if extraction.notified_dead_at is not None:
        return False
    frac = extraction.dead_fraction
    pct = f"{frac * 100:.0f}%" if frac is not None else "—"
    msg = f"Moon **dead** ({pct} good ore mined): {extraction.eve_moon or extraction.structure}."
    _send_webhook(embed=_embed("Whale Tax — Moon Dead", msg, color=0xC0392B))
    extraction.notified_dead_at = eve_now()
    extraction.save(update_fields=["notified_dead_at"])
    return True
