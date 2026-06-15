"""Celery task wiring: the bits that broke in prod, pinned so they can't regress.

`sync_structure_ledger` must read the observer ledger with ``force_refresh=True``:
django-esi caches the ETag under the *path template*, so every structure in the
per-observer fan-out collides on one cache entry. Without force_refresh the first
structure stores an ETag and every later one gets a false 304 -> no rows land,
yet ``last_ledger_sync`` still stamps "done" -> only one structure's mining is
ever visible (see tasks.sync_structure_ledger / _results docstrings).
"""

import datetime as dt
from unittest import mock

from allianceauth.eveonline.models import EveCorporationInfo
from django.test import TestCase
from eveuniverse.models import EveCategory, EveGroup, EveType

from whatax import tasks
from whatax.models import MiningLedgerEntry, MiningStructure


def _ore_type(type_id=46300, name="Bitumens"):
    cat, _ = EveCategory.objects.get_or_create(
        id=25, defaults={"name": "Asteroid", "published": True}
    )
    grp, _ = EveGroup.objects.get_or_create(
        id=1884, defaults={"name": "Moon Materials", "eve_category": cat, "published": True}
    )
    return EveType.objects.create(id=type_id, name=name, eve_group=grp, published=True)


def _ledger_row(character_id, type_id, quantity, last_updated):
    """A stand-in for an ESI observer-ledger row (attribute access, not dict)."""
    row = mock.Mock()
    row.character_id = character_id
    row.type_id = type_id
    row.quantity = quantity
    row.last_updated = last_updated
    row.recorded_corporation_id = 98659319
    return row


class SyncStructureLedgerTest(TestCase):
    def setUp(self):
        self.corp = EveCorporationInfo.objects.create(
            corporation_id=98659319,
            corporation_name="Corp",
            corporation_ticker="CORP",
            member_count=1,
        )
        self.structure = MiningStructure.objects.create(
            structure_id=1043102469926, corporation=self.corp, name="Berta - 056"
        )
        self.ore = _ore_type()

    def _run(self, rows):
        """Run the task with ESI mocked to return ``rows``; return the ESI op mock.

        The operation object's ``.results(...)`` is what ``_results`` calls, so we
        can assert how it was invoked.
        """
        op = mock.Mock()
        op.results.return_value = rows
        industry = tasks.providers.esi.client.Industry
        with mock.patch.object(tasks, "_enabled", return_value=True), mock.patch.object(
            tasks, "_corp_token", return_value=mock.Mock()
        ), mock.patch.object(tasks, "_eve_type", return_value=self.ore), mock.patch.object(
            industry, "GetCorporationCorporationIdMiningObserversObserverId", return_value=op
        ):
            tasks.sync_structure_ledger(self.structure.pk)
        return op

    def test_reads_ledger_with_force_refresh(self):
        """Regression: a per-observer 304 must never silently hide a structure."""
        op = self._run([_ledger_row(700001, 46300, 100, dt.date(2026, 5, 13))])
        op.results.assert_called_once_with(force_refresh=True)

    def test_persists_returned_rows(self):
        self._run(
            [
                _ledger_row(700001, 46300, 100, dt.date(2026, 5, 13)),
                _ledger_row(700002, 46300, 250, dt.date(2026, 5, 14)),
            ]
        )
        entries = MiningLedgerEntry.objects.filter(structure=self.structure)
        self.assertEqual(entries.count(), 2)
        self.assertEqual({e.quantity for e in entries}, {100, 250})
        self.structure.refresh_from_db()
        self.assertIsNotNone(self.structure.last_ledger_sync)

    def test_no_token_skips_without_stamping(self):
        """Missing token must not stamp last_ledger_sync as a successful sync."""
        with mock.patch.object(tasks, "_enabled", return_value=True), mock.patch.object(
            tasks, "_corp_token", return_value=None
        ):
            tasks.sync_structure_ledger(self.structure.pk)
        self.structure.refresh_from_db()
        self.assertEqual(MiningLedgerEntry.objects.count(), 0)
        self.assertIsNone(self.structure.last_ledger_sync)
