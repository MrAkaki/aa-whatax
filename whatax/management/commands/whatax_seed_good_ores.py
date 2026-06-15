"""Seed the global good-ore default with all moon ore types (§11).

Idempotent: re-running only adds missing rows. With ``--recompute`` it also
re-evaluates existing extractions against the (now seeded) good-ore set so the
backlog that was wedged at "popped" with a 0/NULL denominator flips to "dead"
where appropriate. Backfilled transitions are stamped ``notified_dead_at`` so the
historical pops do *not* fire a late Discord webhook (use ``--notify`` to send).
"""

from django.core.management.base import BaseCommand
from eveuniverse.models import EveType

from whatax.core import moons
from whatax.core.timeutils import eve_now


class Command(BaseCommand):
    help = "Seed GoodOreDefault with all moon ore types; optionally recompute extractions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--recompute",
            action="store_true",
            help="Re-evaluate existing (non-dead/cancelled) extractions after seeding.",
        )
        parser.add_argument(
            "--notify",
            action="store_true",
            help="With --recompute, fire the Discord webhook for backfilled dead transitions.",
        )

    def handle(self, *args, **options):
        from whatax.models import GoodOreDefault, MoonExtraction

        type_ids = list(
            EveType.objects.filter(eve_group_id__in=moons.MOON_ORE_GROUP_IDS).values_list(
                "id", flat=True
            )
        )
        created = 0
        for type_id in type_ids:
            _, was_created = GoodOreDefault.objects.get_or_create(ore_type_id=type_id)
            created += int(was_created)
        self.stdout.write(
            f"Good-ore defaults: {created} added, {GoodOreDefault.objects.count()} total "
            f"({len(type_ids)} moon ore types found)."
        )

        if not options["recompute"]:
            return

        from whatax.notifications import notify_moon_dead

        flipped = 0
        actives = MoonExtraction.objects.exclude(
            status__in=[MoonExtraction.Status.DEAD, MoonExtraction.Status.CANCELLED]
        )
        for extraction in actives:
            if moons.recompute_dead(extraction):
                flipped += 1
                if options["notify"]:
                    notify_moon_dead(extraction)
                else:
                    # Suppress a late webhook for this historical transition.
                    extraction.notified_dead_at = eve_now()
                    extraction.save(update_fields=["notified_dead_at"])
        self.stdout.write(f"Recompute: {flipped} extraction(s) transitioned to dead.")
