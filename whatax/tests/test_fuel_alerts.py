"""Low-fuel staff alerts: severity-based reminder cadence and re-arm on refuel."""

import datetime as dt
from unittest import mock

from allianceauth.eveonline.models import EveCorporationInfo
from django.contrib.auth.models import Permission, User
from django.test import TestCase

from whatax import tasks
from whatax.core.timeutils import eve_now
from whatax.models import MiningStructure, MoonExtraction, MoonGroup


def _corp(corporation_id=3001):
    return EveCorporationInfo.objects.create(
        corporation_id=corporation_id,
        corporation_name="Corp",
        corporation_ticker="CORP",
        member_count=1,
    )


class CheckStructureFuelTest(TestCase):
    def setUp(self):
        self.corp = _corp()
        self.now = eve_now()

    def _struct(self, sid, *, days_left=None, last_notified=None):
        expires = None if days_left is None else self.now + dt.timedelta(days=days_left)
        return MiningStructure.objects.create(
            structure_id=sid,
            corporation=self.corp,
            name=f"S{sid}",
            fuel_expires=expires,
            notified_low_fuel_at=last_notified,
        )

    def _run(self, *, warning=7, critical=2):
        config = mock.Mock(fuel_warning_days=warning, fuel_critical_days=critical)
        with mock.patch.object(tasks, "_enabled", return_value=True), mock.patch.object(
            tasks, "get_config", return_value=config
        ), mock.patch("whatax.notifications._dm_staff", return_value=True) as dm:
            tasks.sweep_structure_health()
        return dm

    def _flag(self, structure):
        return MiningStructure.objects.get(pk=structure.pk).notified_low_fuel_at

    def test_alerts_only_below_warning(self):
        low = self._struct(1, days_left=5)
        ok = self._struct(2, days_left=20)
        unknown = self._struct(3, days_left=None)

        dm = self._run()

        self.assertEqual(dm.call_count, 1)
        self.assertIsNotNone(self._flag(low))
        self.assertIsNone(self._flag(ok))
        self.assertIsNone(self._flag(unknown))

    def test_daily_cadence_below_warning(self):
        recent = self._struct(1, days_left=5, last_notified=self.now - dt.timedelta(hours=2))
        due = self._struct(2, days_left=5, last_notified=self.now - dt.timedelta(hours=25))

        dm = self._run()

        # Daily (24h): the 2h-old reminder waits, the 25h-old one re-sends.
        self.assertEqual(dm.call_count, 1)
        self.assertEqual(self._flag(recent), self.now - dt.timedelta(hours=2))
        self.assertGreater(self._flag(due), self.now - dt.timedelta(hours=1))

    def test_critical_cadence_every_six_hours(self):
        recent = self._struct(1, days_left=1, last_notified=self.now - dt.timedelta(hours=3))
        due = self._struct(2, days_left=1, last_notified=self.now - dt.timedelta(hours=7))

        dm = self._run()

        # Critical (6h): the 3h-old reminder waits, the 7h-old one re-sends.
        self.assertEqual(dm.call_count, 1)
        self.assertEqual(self._flag(recent), self.now - dt.timedelta(hours=3))
        self.assertGreater(self._flag(due), self.now - dt.timedelta(hours=1))

    def test_rearms_after_refuel(self):
        low = self._struct(1, days_left=1, last_notified=self.now - dt.timedelta(days=1))
        low.fuel_expires = self.now + dt.timedelta(days=30)
        low.save(update_fields=["fuel_expires"])

        dm = self._run()

        self.assertEqual(dm.call_count, 0)
        self.assertIsNone(self._flag(low))


class DmStaffRecipientsTest(TestCase):
    """End-to-end recipient resolution: permission holders -> aadiscordbot.send_message."""

    def setUp(self):
        self.corp = _corp()
        self.now = eve_now()

    def _grant(self, username, codename):
        user = User.objects.create(username=username)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="whatax", codename=codename)
        )
        return user

    def test_low_fuel_dms_permission_holders(self):
        viewer = self._grant("viewer", "view_structures")
        staff = self._grant("staff", "manage_payments")
        self._grant("outsider", "basic_access")
        MiningStructure.objects.create(
            structure_id=1,
            corporation=self.corp,
            name="S1",
            fuel_expires=self.now + dt.timedelta(days=3),
        )

        config = mock.Mock(fuel_warning_days=7, fuel_critical_days=2)
        with mock.patch.object(tasks, "_enabled", return_value=True), mock.patch.object(
            tasks, "get_config", return_value=config
        ), mock.patch("aadiscordbot.tasks.send_message") as send:
            tasks.sweep_structure_health()

        recipients = {call.kwargs["user"] for call in send.call_args_list}
        self.assertIn(viewer, recipients)
        self.assertIn(staff, recipients)
        self.assertNotIn(User.objects.get(username="outsider"), recipients)


class PopDriftAlertTest(TestCase):
    def setUp(self):
        self.corp = _corp()
        self.now = eve_now()
        self.group = MoonGroup.objects.create(name="Weekly", schedule_interval_days=7)

    def _struct(self, sid, *, planned=None, drift_at=None):
        return MiningStructure.objects.create(
            structure_id=sid,
            corporation=self.corp,
            name=f"S{sid}",
            group=self.group,
            planned_pop_at=planned,
            notified_drift_at=drift_at,
        )

    def _extraction(self, structure, when):
        return MoonExtraction.objects.create(
            structure=structure,
            extraction_start_time=when - dt.timedelta(days=2),
            chunk_arrival_time=when,
            status=MoonExtraction.Status.SCHEDULED,
        )

    def _run(self):
        config = mock.Mock(fuel_warning_days=7, fuel_critical_days=2)
        with mock.patch.object(tasks, "_enabled", return_value=True), mock.patch.object(
            tasks, "get_config", return_value=config
        ), mock.patch("whatax.notifications._dm_staff", return_value=True) as dm:
            tasks.sweep_structure_health()
        return dm

    def _flag(self, structure):
        return MiningStructure.objects.get(pk=structure.pk).notified_drift_at

    def test_dms_once_on_drift(self):
        # next pop now+2 projects to now+9; planned says now+30 -> off schedule.
        s = self._struct(10, planned=self.now + dt.timedelta(days=30))
        self._extraction(s, self.now + dt.timedelta(days=2))

        dm = self._run()
        self.assertEqual(dm.call_count, 1)
        self.assertIsNotNone(self._flag(s))

        # Still drifted on the next sweep: no repeat DM (unique message).
        self.assertEqual(self._run().call_count, 0)

    def test_no_dm_when_on_schedule(self):
        # next pop now+2 projects to now+9, matching the planned date.
        s = self._struct(11, planned=self.now + dt.timedelta(days=9))
        self._extraction(s, self.now + dt.timedelta(days=2))

        self.assertEqual(self._run().call_count, 0)
        self.assertIsNone(self._flag(s))

    def test_clears_when_realigned(self):
        s = self._struct(
            12,
            planned=self.now + dt.timedelta(days=9),
            drift_at=self.now - dt.timedelta(days=1),
        )
        self._extraction(s, self.now + dt.timedelta(days=2))  # back on schedule

        self.assertEqual(self._run().call_count, 0)
        self.assertIsNone(self._flag(s))
