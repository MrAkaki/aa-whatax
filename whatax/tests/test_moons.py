"""Pure tests for sec-class banding and notification parsing (no DB needed)."""

import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase

from whatax.core import moons


class SecClassTest(SimpleTestCase):
    def test_banding(self):
        self.assertEqual(moons.sec_class(1.0), moons.HIGHSEC)
        self.assertEqual(moons.sec_class(0.6), moons.HIGHSEC)
        self.assertEqual(moons.sec_class(0.5), moons.HIGHSEC)
        self.assertEqual(moons.sec_class(0.4), moons.LOWSEC)
        self.assertEqual(moons.sec_class(0.3), moons.LOWSEC)
        self.assertEqual(moons.sec_class(0.0), moons.NULLSEC)
        self.assertEqual(moons.sec_class(-0.3), moons.NULLSEC)


class LdapTimeTest(SimpleTestCase):
    def test_known_value(self):
        # 2021-01-01T00:00:00Z as Windows FILETIME ticks.
        ticks = (dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() + 11644473600) * 10_000_000
        parsed = moons.ldap_to_datetime(int(ticks))
        self.assertEqual(parsed.year, 2021)
        self.assertEqual(parsed.month, 1)
        self.assertEqual(parsed.tzinfo, dt.timezone.utc)

    def test_rounds_to_whole_second(self):
        # Sub-second FILETIME ticks must round to a whole second.
        base = dt.datetime(2026, 8, 11, 10, 1, 2, tzinfo=dt.timezone.utc)
        ticks = int((base.timestamp() + 11644473600) * 10_000_000) - 901_000  # ~90 ms earlier
        parsed = moons.ldap_to_datetime(ticks)
        self.assertEqual(parsed.microsecond, 0)
        self.assertEqual(parsed, base)


class ParseExtractionStartedTest(SimpleTestCase):
    def test_parses_ore_volume_and_times(self):
        ready = int((dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc).timestamp() + 11644473600) * 10_000_000)
        auto = int((dt.datetime(2026, 6, 4, tzinfo=dt.timezone.utc).timestamp() + 11644473600) * 10_000_000)
        text = (
            f"autoTime: {auto}\n"
            "moonID: 40161708\n"
            "oreVolumeByType:\n"
            "  45494: 1500000.5\n"
            "  46676: 2000000\n"
            f"readyTime: {ready}\n"
            "structureID: 1000000000001\n"
        )
        parsed = moons.parse_extraction_started(text)
        self.assertEqual(parsed["structure_id"], 1000000000001)
        self.assertEqual(parsed["moon_id"], 40161708)
        self.assertEqual(parsed["chunk_arrival_time"].day, 1)
        self.assertEqual(parsed["natural_decay_time"].day, 4)
        self.assertEqual(parsed["ore_volume_by_type"][45494], Decimal("1500000.5"))
        self.assertEqual(parsed["ore_volume_by_type"][46676], Decimal("2000000"))
