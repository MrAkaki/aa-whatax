"""Staff structures list: next-pop annotation + staff-page rendering."""

import datetime as dt

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from django.contrib.auth.models import Permission, User
from django.test import TestCase

from whatax.core.timeutils import eve_now
from whatax.models import MiningStructure, MoonExtraction, MoonGroup
from whatax.views import _group_by_moongroup, _structures_next_pop


def _char(character_id, corp_id=3001):
    return EveCharacter.objects.create(
        character_id=character_id,
        character_name=f"Char {character_id}",
        corporation_id=corp_id,
        corporation_name="Corp",
        corporation_ticker="CORP",
    )


def _corp(corporation_id=3001):
    return EveCorporationInfo.objects.create(
        corporation_id=corporation_id,
        corporation_name="Corp",
        corporation_ticker="CORP",
        member_count=1,
    )


class StructuresNextPopTest(TestCase):
    def setUp(self):
        self.corp = _corp()
        self.now = eve_now()

    def _struct(self, sid, name):
        return MiningStructure.objects.create(
            structure_id=sid, corporation=self.corp, name=name
        )

    def _extraction(self, structure, when, status=MoonExtraction.Status.SCHEDULED):
        return MoonExtraction.objects.create(
            structure=structure,
            extraction_start_time=when - dt.timedelta(days=2),
            chunk_arrival_time=when,
            status=status,
        )

    def test_next_pop_is_earliest_future_extraction(self):
        s = self._struct(900100, "A")
        self._extraction(s, self.now + dt.timedelta(days=5))
        self._extraction(s, self.now + dt.timedelta(days=2))  # earliest future
        self._extraction(s, self.now + dt.timedelta(days=9))
        row = _structures_next_pop().get(pk=s.pk)
        self.assertEqual(row.next_pop, self.now + dt.timedelta(days=2))

    def test_past_and_closed_extractions_ignored(self):
        s = self._struct(900101, "B")
        self._extraction(s, self.now - dt.timedelta(days=1))  # already arrived
        self._extraction(
            s, self.now + dt.timedelta(days=3), status=MoonExtraction.Status.POPPED
        )
        self._extraction(
            s, self.now + dt.timedelta(days=4), status=MoonExtraction.Status.CANCELLED
        )
        row = _structures_next_pop().get(pk=s.pk)
        self.assertIsNone(row.next_pop)

    def test_structure_without_extractions_has_no_next_pop(self):
        s = self._struct(900102, "C")
        row = _structures_next_pop().get(pk=s.pk)
        self.assertIsNone(row.next_pop)

    def test_all_structures_are_listed(self):
        self._struct(900103, "X")
        self._struct(900104, "Y")
        self.assertEqual(_structures_next_pop().count(), 2)


class PlannedPopTest(TestCase):
    """planned_pop_at = next pop + the group's schedule_interval_days."""

    def setUp(self):
        self.corp = _corp()
        self.now = eve_now()
        self.group = MoonGroup.objects.create(name="Weekly", schedule_interval_days=7)

    def _struct(self, sid, name, group=None):
        return MiningStructure.objects.create(
            structure_id=sid, corporation=self.corp, name=name, group=group
        )

    def _extraction(self, structure, when, status=MoonExtraction.Status.SCHEDULED):
        return MoonExtraction.objects.create(
            structure=structure,
            extraction_start_time=when - dt.timedelta(days=2),
            chunk_arrival_time=when,
            status=status,
        )

    def test_planned_pop_is_next_pop_plus_group_interval(self):
        s = self._struct(910100, "A", group=self.group)
        self._extraction(s, self.now + dt.timedelta(days=2))  # next pop
        self._extraction(s, self.now + dt.timedelta(days=9))
        s.recompute_planned_pop()
        s.refresh_from_db()
        self.assertEqual(
            s.planned_pop_at, self.now + dt.timedelta(days=2) + dt.timedelta(days=7)
        )

    def test_ungrouped_structure_has_no_planned_pop(self):
        s = self._struct(910101, "B")  # group=None -> no cadence
        self._extraction(s, self.now + dt.timedelta(days=2))
        s.recompute_planned_pop()
        s.refresh_from_db()
        self.assertIsNone(s.planned_pop_at)

    def test_no_future_extraction_clears_planned_pop(self):
        s = self._struct(910102, "C", group=self.group)
        s.planned_pop_at = self.now  # stale value to be cleared
        s.save(update_fields=["planned_pop_at"])
        self._extraction(s, self.now - dt.timedelta(days=1))  # already arrived
        s.recompute_planned_pop()
        s.refresh_from_db()
        self.assertIsNone(s.planned_pop_at)


class GroupByMoonGroupTest(TestCase):
    def setUp(self):
        self.corp = _corp()

    def _struct(self, sid, name, group=None):
        return MiningStructure.objects.create(
            structure_id=sid, corporation=self.corp, name=name, group=group
        )

    def test_one_bucket_per_group_sorted_with_ungrouped_last(self):
        beta = MoonGroup.objects.create(name="Beta", schedule_interval_days=7)
        alpha = MoonGroup.objects.create(name="Alpha", schedule_interval_days=3)
        b = self._struct(910001, "B", group=beta)
        a = self._struct(910002, "A", group=alpha)
        u = self._struct(910003, "U")  # no group

        buckets = _group_by_moongroup([b, a, u], lambda s: s.group)

        # Named groups first, alphabetical by name; ungrouped bucket trails.
        self.assertEqual([x["group"] for x in buckets], [alpha, beta, None])
        self.assertEqual(buckets[0]["items"], [a])
        self.assertEqual(buckets[1]["items"], [b])
        self.assertEqual(buckets[2]["items"], [u])

    def test_ungrouped_bucket_omitted_when_all_grouped(self):
        alpha = MoonGroup.objects.create(name="Alpha", schedule_interval_days=3)
        a = self._struct(910010, "A", group=alpha)
        buckets = _group_by_moongroup([a], lambda s: s.group)
        self.assertEqual([x["group"] for x in buckets], [alpha])


class StaffStructuresViewTest(TestCase):
    def setUp(self):
        self.corp = _corp()
        self.user = User.objects.create_user(username="staffer2", password="pw")
        perm = Permission.objects.get(
            content_type__app_label="whatax", codename="manage_payments"
        )
        self.user.user_permissions.add(perm)
        # AllianceAuth gates app pages behind a registered main character.
        main = _char(222333)
        CharacterOwnership.objects.create(character=main, owner_hash="mh2", user=self.user)
        profile = self.user.profile
        profile.main_character = main
        profile.save()

    def test_staff_page_lists_structures_with_next_pop(self):
        s = MiningStructure.objects.create(
            structure_id=900200, corporation=self.corp, name="Refinery Z"
        )
        MoonExtraction.objects.create(
            structure=s,
            extraction_start_time=eve_now() + dt.timedelta(days=1),
            chunk_arrival_time=eve_now() + dt.timedelta(days=3),
        )
        self.client.force_login(self.user)
        resp = self.client.get("/whatax/staff/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Structures")
        self.assertContains(resp, "Refinery Z")
        # A structure in no group lands in the trailing "Ungrouped" bucket.
        groups = resp.context["structure_groups"]
        self.assertEqual(len(groups), 1)
        self.assertIsNone(groups[0]["group"])
        structures = groups[0]["items"]
        self.assertEqual(len(structures), 1)
        self.assertIsNotNone(structures[0].next_pop)
