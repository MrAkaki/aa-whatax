"""Singleton config accessor."""


def get_config():
    """Return the TaxConfiguration solo instance (deferred import avoids a cycle)."""
    from whatax.models import TaxConfiguration

    return TaxConfiguration.objects.get_solo()
