"""Staff structures list: next-pop annotation + staff-page rendering."""

import datetime as dt

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter, EveCorporationInfo
from django.contrib.auth.models import Permission, User
from django.test import TestCase

from whatax.core.timeutils import eve_now
from whatax.models import MiningStructure, MoonExtraction, MoonGroup
from whatax.views import (
    _group_by_moongroup,
    _structure_pop_warnings,
    _structures_next_pop,
)


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

    def test_no_future_extraction_keeps_projection(self):
        # The projection is sticky: once a pop fires and the next cycle isn't
        # scheduled yet, the standing planned pop is kept so it can be compared
        # against the reset extraction when it lands.
        s = self._struct(910102, "C", group=self.group)
        projection = self.now + dt.timedelta(days=7)
        s.planned_pop_at = projection
        s.save(update_fields=["planned_pop_at"])
        self._extraction(s, self.now - dt.timedelta(days=1))  # already arrived
        s.recompute_planned_pop()
        s.refresh_from_db()
        self.assertEqual(s.planned_pop_at, projection)

    def test_removing_group_clears_planned_pop(self):
        s = self._struct(910103, "D", group=self.group)
        s.planned_pop_at = self.now + dt.timedelta(days=7)
        s.save(update_fields=["planned_pop_at"])
        s.group = None
        s.recompute_planned_pop()
        s.refresh_from_db()
        self.assertIsNone(s.planned_pop_at)

    def test_on_schedule_reset_reprojects(self):
        # planned holds the projected next reset (T1 + interval). When the reset
        # lands on that day, the projection advances one more interval.
        s = self._struct(910104, "E", group=self.group)
        t1 = self.now + dt.timedelta(days=2)
        self._extraction(s, t1)
        s.recompute_planned_pop()
        s.refresh_from_db()
        self.assertEqual(s.planned_pop_at, t1 + dt.timedelta(days=7))
        # T1 pops; the next cycle is scheduled on the planned day.
        s.extractions.update(status=MoonExtraction.Status.POPPED)
        reset = t1 + dt.timedelta(days=7)
        self._extraction(s, reset)
        s.recompute_planned_pop()
        s.refresh_from_db()
        self.assertEqual(s.planned_pop_at, reset + dt.timedelta(days=7))

    def test_off_schedule_reset_keeps_projection(self):
        s = self._struct(910105, "F", group=self.group)
        t1 = self.now + dt.timedelta(days=2)
        self._extraction(s, t1)
        s.recompute_planned_pop()
        s.refresh_from_db()
        planned = s.planned_pop_at  # t1 + 7
        # T1 pops; the reset lands two days off the planned day.
        s.extractions.update(status=MoonExtraction.Status.POPPED)
        self._extraction(s, planned + dt.timedelta(days=2))
        s.recompute_planned_pop()
        s.refresh_from_db()
        self.assertEqual(s.planned_pop_at, planned)  # kept -> deviation visible

    def test_accept_takes_new_schedule(self):
        s = self._struct(910106, "G", group=self.group)
        t1 = self.now + dt.timedelta(days=2)
        self._extraction(s, t1)
        s.recompute_planned_pop()
        s.refresh_from_db()
        planned = s.planned_pop_at
        s.extractions.update(status=MoonExtraction.Status.POPPED)
        reset = planned + dt.timedelta(days=2)
        self._extraction(s, reset)
        s.recompute_planned_pop(accept=True)
        s.refresh_from_db()
        self.assertEqual(s.planned_pop_at, reset + dt.timedelta(days=7))


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


class StructurePopWarningsTest(TestCase):
    def setUp(self):
        self.corp = _corp()
        self.now = eve_now()
        self.group = MoonGroup.objects.create(name="Weekly", schedule_interval_days=7)

    def _struct(self, sid, name, group=None, is_active=True):
        return MiningStructure.objects.create(
            structure_id=sid, corporation=self.corp, name=name,
            group=group, is_active=is_active,
        )

    def _extraction(self, structure, when):
        return MoonExtraction.objects.create(
            structure=structure,
            extraction_start_time=when - dt.timedelta(days=2),
            chunk_arrival_time=when,
            status=MoonExtraction.Status.SCHEDULED,
        )

    def test_ungrouped_structure_flagged_as_no_setup(self):
        s = self._struct(920100, "A")  # no group
        self._extraction(s, self.now + dt.timedelta(days=2))
        no_setup, off = _structure_pop_warnings(_structures_next_pop())
        self.assertEqual([w["structure"] for w in no_setup], [s])
        self.assertEqual(off, [])

    def test_no_upcoming_pop_flagged_as_no_setup(self):
        s = self._struct(920101, "B", group=self.group)  # grouped, no extraction
        no_setup, off = _structure_pop_warnings(_structures_next_pop())
        self.assertEqual([w["structure"] for w in no_setup], [s])

    def test_inactive_structure_not_flagged(self):
        self._struct(920102, "C", is_active=False)  # ungrouped + inactive
        no_setup, off = _structure_pop_warnings(_structures_next_pop())
        self.assertEqual(no_setup, [])
        self.assertEqual(off, [])

    def test_on_schedule_structure_not_flagged(self):
        s = self._struct(920103, "D", group=self.group)
        self._extraction(s, self.now + dt.timedelta(days=2))
        s.recompute_planned_pop()  # planned = next_pop + 7, on schedule
        no_setup, off = _structure_pop_warnings(_structures_next_pop())
        self.assertEqual(no_setup, [])
        self.assertEqual(off, [])

    def test_off_schedule_structure_flagged(self):
        s = self._struct(920104, "E", group=self.group)
        self._extraction(s, self.now + dt.timedelta(days=2))
        s.recompute_planned_pop()
        s.extractions.update(status=MoonExtraction.Status.POPPED)
        # Reset two days off the planned day -> deviation kept.
        self._extraction(s, s.planned_pop_at + dt.timedelta(days=2))
        s.recompute_planned_pop()
        no_setup, off = _structure_pop_warnings(_structures_next_pop())
        self.assertEqual(no_setup, [])
        self.assertEqual([w["structure"] for w in off], [s])


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

    def test_staff_page_renders_warning_panels(self):
        group = MoonGroup.objects.create(name="Weekly", schedule_interval_days=7)
        now = eve_now()
        # Off-schedule drill: popped first cycle, reset two days off planned.
        off = MiningStructure.objects.create(
            structure_id=900400, corporation=self.corp, name="Drift Refinery", group=group
        )
        MoonExtraction.objects.create(
            structure=off, extraction_start_time=now,
            chunk_arrival_time=now - dt.timedelta(days=1),
            status=MoonExtraction.Status.POPPED,
        )
        MoonExtraction.objects.create(
            structure=off, extraction_start_time=now,
            chunk_arrival_time=now + dt.timedelta(days=11),
            status=MoonExtraction.Status.SCHEDULED,
        )
        off.planned_pop_at = now + dt.timedelta(days=9)
        off.save(update_fields=["planned_pop_at"])
        # No-setup drill: ungrouped.
        MiningStructure.objects.create(
            structure_id=900401, corporation=self.corp, name="Lonely Refinery"
        )
        self.client.force_login(self.user)
        resp = self.client.get("/whatax/staff/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Drift Refinery")
        self.assertContains(resp, "Dismiss")
        self.assertContains(resp, "Lonely Refinery")
        self.assertContains(resp, "not assigned to a group")

    def test_dismiss_pop_accepts_new_schedule(self):
        group = MoonGroup.objects.create(name="Weekly", schedule_interval_days=7)
        s = MiningStructure.objects.create(
            structure_id=900300, corporation=self.corp, name="Refinery Q", group=group
        )
        now = eve_now()
        MoonExtraction.objects.create(
            structure=s,
            extraction_start_time=now,
            chunk_arrival_time=now + dt.timedelta(days=2),
            status=MoonExtraction.Status.POPPED,
        )
        reset = now + dt.timedelta(days=11)
        MoonExtraction.objects.create(
            structure=s,
            extraction_start_time=reset - dt.timedelta(days=2),
            chunk_arrival_time=reset,
            status=MoonExtraction.Status.SCHEDULED,
        )
        s.planned_pop_at = now + dt.timedelta(days=9)  # off-schedule deviation
        s.save(update_fields=["planned_pop_at"])
        self.client.force_login(self.user)
        resp = self.client.post(f"/whatax/staff/structure/{s.id}/dismiss-pop/")
        self.assertRedirects(resp, "/whatax/staff/")
        s.refresh_from_db()
        self.assertEqual(s.planned_pop_at, reset + dt.timedelta(days=7))


class StructuresReadViewTest(TestCase):
    """The standalone view_structures read role: pops & warnings, no payments."""

    def setUp(self):
        self.corp = _corp()
        self.user = User.objects.create_user(username="driller", password="pw")
        perm = Permission.objects.get(
            content_type__app_label="whatax", codename="view_structures"
        )
        self.user.user_permissions.add(perm)
        main = _char(222444)
        CharacterOwnership.objects.create(character=main, owner_hash="mh3", user=self.user)
        profile = self.user.profile
        profile.main_character = main
        profile.save()

    def _off_schedule_drill(self):
        group = MoonGroup.objects.create(name="Weekly", schedule_interval_days=7)
        now = eve_now()
        off = MiningStructure.objects.create(
            structure_id=930400, corporation=self.corp, name="Drift Refinery", group=group
        )
        MoonExtraction.objects.create(
            structure=off, extraction_start_time=now,
            chunk_arrival_time=now - dt.timedelta(days=1),
            status=MoonExtraction.Status.POPPED,
        )
        MoonExtraction.objects.create(
            structure=off, extraction_start_time=now,
            chunk_arrival_time=now + dt.timedelta(days=11),
            status=MoonExtraction.Status.SCHEDULED,
        )
        off.planned_pop_at = now + dt.timedelta(days=9)
        off.save(update_fields=["planned_pop_at"])
        return off

    def test_page_lists_structures_and_warnings(self):
        self._off_schedule_drill()
        MiningStructure.objects.create(
            structure_id=930401, corporation=self.corp, name="Lonely Refinery"
        )
        self.client.force_login(self.user)
        resp = self.client.get("/whatax/structures/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Drift Refinery")
        self.assertContains(resp, "Lonely Refinery")
        self.assertContains(resp, "not assigned to a group")

    def test_read_role_sees_no_dismiss_action_or_payment_data(self):
        self._off_schedule_drill()
        self.client.force_login(self.user)
        resp = self.client.get("/whatax/structures/")
        # Read-only: the off-schedule warning shows but the dismiss form (the only
        # mutation) is absent — match its endpoint, not the word "Dismiss" (the AA
        # chrome uses it elsewhere).
        self.assertNotContains(resp, "dismiss-pop")
        # No payment/record surface leaks onto this page.
        self.assertNotContains(resp, "Unmatched payments")
        self.assertNotContains(resp, "Add payment")

    def test_basic_access_only_user_cannot_open_structures(self):
        other = User.objects.create_user(username="plainuser", password="pw")
        other.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="whatax", codename="basic_access"
            )
        )
        self.client.force_login(other)
        resp = self.client.get("/whatax/structures/")
        self.assertNotEqual(resp.status_code, 200)
