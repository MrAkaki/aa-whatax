"""QuerySet / Manager logic, including object-level visibility scoping."""

from django.db import models


class TaxConfigurationManager(models.Manager):
    """Singleton accessor for the one config row (pk=1)."""

    def get_solo(self):
        """Return the config row, creating it if absent."""
        obj, _ = self.get_or_create(pk=1)
        return obj


class TaxRecordQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Self-only unless the caller can manage payments or administer."""
        if user.has_perm("whatax.manage_payments") or user.has_perm("whatax.admin_access"):
            return self
        return self.filter(user=user)


class TaxRecordManager(models.Manager.from_queryset(TaxRecordQuerySet)):
    pass


class MiningStructureQuerySet(models.QuerySet):
    def active(self):
        """Structures whose mining counts toward tax (per-structure toggle)."""
        return self.filter(is_active=True)


class MiningStructureManager(models.Manager.from_queryset(MiningStructureQuerySet)):
    pass


class MiningSnapshotQuerySet(models.QuerySet):
    def visible_to(self, user):
        if user.has_perm("whatax.manage_payments") or user.has_perm("whatax.admin_access"):
            return self
        return self.filter(user=user)


class MiningSnapshotManager(models.Manager.from_queryset(MiningSnapshotQuerySet)):
    pass
