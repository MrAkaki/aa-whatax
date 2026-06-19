"""Refined-value computation: value the minerals an ore reprocesses into."""

from decimal import Decimal
from typing import Protocol

from eveuniverse.models import EveType, EveTypeMaterial

from whatax.providers import JaniceClient, JaniceError

__all__ = ["PriceProvider", "ReprocessPriceProvider", "provider_from_config"]


class PriceProvider(Protocol):
    def refined_value(self, ore_type: EveType, quantity: int) -> Decimal: ...


class ReprocessPriceProvider:
    """Reprocess-then-price provider driven by a ``TaxConfiguration`` row.

    ``janice`` is injectable for testing; pass a stub exposing
    ``prices(type_ids, *, basis) -> {type_id: Decimal}``.
    """

    def __init__(self, *, reprocessing_yield: Decimal, basis: str, janice):
        self.reprocessing_yield = Decimal(reprocessing_yield)
        self.basis = basis
        self._janice = janice

    def refined_value(self, ore_type: EveType, quantity: int) -> Decimal:
        if quantity <= 0:
            return Decimal("0")
        materials = list(EveTypeMaterial.objects.filter(eve_type=ore_type))
        if not materials:
            # No reprocessing recipe loaded for this ore; fail loud rather than value at zero.
            raise JaniceError(f"no reprocessing materials for ore type {ore_type.id}")
        portion = Decimal(ore_type.portion_size or 1)
        batches = Decimal(quantity) / portion
        prices = self._janice.prices(
            [m.material_eve_type_id for m in materials], basis=self.basis
        )
        total = Decimal("0")
        for m in materials:
            price = prices.get(m.material_eve_type_id)
            if price is None:
                raise JaniceError(f"no price for mineral {m.material_eve_type_id}")
            total += Decimal(m.quantity) * batches * self.reprocessing_yield * price
        return total


def provider_from_config(config) -> ReprocessPriceProvider:
    """Build a provider from a ``TaxConfiguration`` row (live Janice client)."""
    return ReprocessPriceProvider(
        reprocessing_yield=config.reprocessing_yield,
        basis=config.mineral_price_basis,
        janice=JaniceClient(config.janice_api_key),
    )
