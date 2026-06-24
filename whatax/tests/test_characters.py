"""Characters tab: allowed roster (by group) + KOS list and its admin management."""

from unittest.mock import patch

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from eveuniverse.models import EveEntity

from whatax.models import KosCharacter, TaxConfiguration


def _char(character_id, corp_id=3001):
    return EveCharacter.objects.create(
        character_id=character_id,
        character_name=f"Char {character_id}",
        corporation_id=corp_id,
        corporation_name="Corp",
        corporation_ticker="CORP",
    )


def _user(username, *codenames):
    user = User.objects.create_user(username=username, password="pw")
    for codename in codenames:
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="whatax", codename=codename)
        )
    return user


def _own(user, char, owner_hash, *, main=False):
    CharacterOwnership.objects.create(character=char, owner_hash=owner_hash, user=user)
    if main:
        profile = user.profile
        profile.main_character = char
        profile.save()


def _member(username, char_id, *codenames):
    """A user with the given perms and a main character (AA gates pages on one)."""
    user = _user(username, *codenames)
    _own(user, _char(char_id), f"h{char_id}", main=True)
    return user


class AllowedRosterTest(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="Allowed")
        self.member = _user("member", "basic_access")
        self.member.groups.add(self.group)
        main = _char(101)
        alt = _char(102)
        _own(self.member, main, "h1", main=True)
        _own(self.member, alt, "h2")

        # An out-of-group player whose characters must NOT show on the roster.
        outsider = _user("outsider")
        _own(outsider, _char(103), "h3")

        config = TaxConfiguration.objects.get_solo()
        config.allowed_group = self.group
        config.save()

    def test_roster_lists_group_members_characters_only(self):
        self.client.force_login(self.member)
        resp = self.client.get("/whatax/characters/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Char 101")  # main / player + character
        self.assertContains(resp, "Char 102")  # alt character
        self.assertNotContains(resp, "Char 103")  # out-of-group character

    def test_unconfigured_group_shows_hint(self):
        config = TaxConfiguration.objects.get_solo()
        config.allowed_group = None
        config.save()
        self.client.force_login(self.member)
        resp = self.client.get("/whatax/characters/")
        self.assertContains(resp, "No allowed group is configured")

    def test_anonymous_is_redirected(self):
        resp = self.client.get("/whatax/characters/")
        self.assertEqual(resp.status_code, 302)


class KosManagementTest(TestCase):
    def setUp(self):
        self.admin = _member("kosadmin", 201, "admin_access")
        self.entity = EveEntity.objects.create(
            id=95000001, name="Bad Guy", category=EveEntity.CATEGORY_CHARACTER
        )

    def test_add_resolves_and_creates_entry(self):
        self.client.force_login(self.admin)
        with patch("whatax.views._resolve_character_entity", return_value=self.entity):
            resp = self.client.post(
                "/whatax/admin/kos/",
                {"character_name": "Bad Guy", "reason": "ganker"},
            )
        self.assertRedirects(resp, "/whatax/admin/kos/")
        entry = KosCharacter.objects.get(character=self.entity)
        self.assertEqual(entry.reason, "ganker")
        self.assertEqual(entry.added_by, self.admin)

    def test_add_unknown_name_creates_nothing(self):
        self.client.force_login(self.admin)
        with patch("whatax.views._resolve_character_entity", return_value=None):
            self.client.post("/whatax/admin/kos/", {"character_name": "Nobody"})
        self.assertFalse(KosCharacter.objects.exists())

    def test_kos_shows_on_characters_tab_for_basic_user(self):
        KosCharacter.objects.create(character=self.entity, added_by=self.admin)
        viewer = _member("viewer", 202, "basic_access")
        self.client.force_login(viewer)
        resp = self.client.get("/whatax/characters/")
        self.assertContains(resp, "Bad Guy")

    def test_delete_removes_entry(self):
        entry = KosCharacter.objects.create(character=self.entity, added_by=self.admin)
        self.client.force_login(self.admin)
        resp = self.client.post(f"/whatax/admin/kos/{entry.pk}/delete/")
        self.assertRedirects(resp, "/whatax/admin/kos/")
        self.assertFalse(KosCharacter.objects.filter(pk=entry.pk).exists())
