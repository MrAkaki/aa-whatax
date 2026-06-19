"""Celery task wiring regressions."""

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
    """Stand-in for an ESI observer-ledger row."""
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
        """Run the task with ESI mocked to return ``rows``; return the ESI op mock."""
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
        with mock.patch.object(tasks, "_enabled", return_value=True), mock.patch.object(
            tasks, "_corp_token", return_value=None
        ):
            tasks.sync_structure_ledger(self.structure.pk)
        self.structure.refresh_from_db()
        self.assertEqual(MiningLedgerEntry.objects.count(), 0)
        self.assertIsNone(self.structure.last_ledger_sync)


class ApplyExtractionStartedTest(TestCase):
    """_apply_extraction_started must not resurrect an already-popped chunk."""

    def setUp(self):
        from whatax.models import MoonExtraction

        self.corp = EveCorporationInfo.objects.create(
            corporation_id=1, corporation_name="C", corporation_ticker="C", member_count=1
        )
        self.structure = MiningStructure.objects.create(
            structure_id=100, corporation=self.corp, name="S"
        )
        self.arrival = dt.datetime(2026, 6, 9, 3, 0, 59, tzinfo=dt.timezone.utc)
        self.popped = MoonExtraction.objects.create(
            structure=self.structure,
            extraction_start_time=self.arrival,
            chunk_arrival_time=self.arrival,
            status=MoonExtraction.Status.POPPED,
            popped_at=dt.datetime(2026, 6, 9, 6, 38, tzinfo=dt.timezone.utc),
        )

    def _started_note(self):
        ticks = int((self.arrival.timestamp() + 11644473600) * 10_000_000)
        note = mock.Mock()
        note.timestamp = self.arrival
        note.text = (
            f"readyTime: {ticks}\n"
            f"structureID: {self.structure.structure_id}\n"
            "oreVolumeByType: {}\n"
        )
        return note

    def test_started_does_not_revive_popped(self):
        from whatax.models import MoonExtraction

        tasks._apply_extraction_started(self._started_note())
        self.popped.refresh_from_db()
        self.assertEqual(self.popped.status, MoonExtraction.Status.POPPED)
        self.assertEqual(MoonExtraction.objects.count(), 1)  # matched, not duplicated
