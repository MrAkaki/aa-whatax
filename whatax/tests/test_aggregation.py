"""Player resolution: UNATTRIBUTED sentinel, no-main fallback, main corp."""

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from django.contrib.auth.models import User
from django.test import TestCase

from whatax.core import aggregation


def _char(character_id, corp_id=3001):
    return EveCharacter.objects.create(
        character_id=character_id,
        character_name=f"Char {character_id}",
        corporation_id=corp_id,
        corporation_name="Corp",
        corporation_ticker="CORP",
    )


class ResolvePlayerTest(TestCase):
    def test_unowned_goes_to_sentinel(self):
        res = aggregation.resolve_player(123456)
        self.assertEqual(res.user.username, aggregation.UNATTRIBUTED_USERNAME)
        self.assertFalse(res.user.is_active)
        self.assertFalse(res.has_main)

    def test_owned_without_main(self):
        user = User.objects.create(username="nomain")
        char = _char(700001)
        CharacterOwnership.objects.create(character=char, owner_hash="hh1", user=user)
        res = aggregation.resolve_player(700001)
        self.assertEqual(res.user, user)
        self.assertFalse(res.has_main)
        self.assertIsNone(res.main_corporation_id)

    def test_owned_with_main(self):
        user = User.objects.create(username="hasmain")
        char = _char(700002, corp_id=4242)
        CharacterOwnership.objects.create(character=char, owner_hash="hh2", user=user)
        profile = user.profile
        profile.main_character = char
        profile.save()
        res = aggregation.resolve_player(700002)
        self.assertEqual(res.user, user)
        self.assertTrue(res.has_main)
        self.assertEqual(res.main_corporation_id, 4242)

    def test_sentinel_is_stable(self):
        first = aggregation.unattributed_user()
        second = aggregation.unattributed_user()
        self.assertEqual(first.pk, second.pk)
