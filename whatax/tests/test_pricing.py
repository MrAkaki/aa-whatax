"""Refined-value math + fail-loud, with EveTypeMaterial and Janice mocked."""

from decimal import Decimal
from unittest import mock

from django.test import SimpleTestCase

from whatax.core import pricing
from whatax.providers import JaniceError


class _Material:
    def __init__(self, material_eve_type_id, quantity):
        self.material_eve_type_id = material_eve_type_id
        self.quantity = quantity


class _Ore:
    id = 1230
    portion_size = 100


class _StubJanice:
    def __init__(self, prices):
        self._prices = prices

    def prices(self, type_ids, *, basis):
        return {t: self._prices[t] for t in type_ids}


class RefinedValueTest(SimpleTestCase):
    def _provider(self, janice, yield_=Decimal("1.0")):
        return pricing.ReprocessPriceProvider(
            reprocessing_yield=yield_, basis="split_immediate", janice=janice
        )

    def test_basic_math(self):
        # 100 units ore = 1 batch; batch yields 10x@2 + 5x@4 = 40 ISK; yield 0.9.
        materials = [_Material(34, 10), _Material(35, 5)]
        janice = _StubJanice({34: Decimal("2"), 35: Decimal("4")})
        with mock.patch.object(
            pricing.EveTypeMaterial.objects, "filter", return_value=materials
        ):
            provider = self._provider(janice, yield_=Decimal("0.9"))
            value = provider.refined_value(_Ore(), 100)
        # (10*2 + 5*4) * 1 batch * 0.9 = 40 * 0.9 = 36
        self.assertEqual(value, Decimal("36.0"))

    def test_scales_with_batches(self):
        materials = [_Material(34, 10)]
        janice = _StubJanice({34: Decimal("1")})
        with mock.patch.object(
            pricing.EveTypeMaterial.objects, "filter", return_value=materials
        ):
            provider = self._provider(janice)
            value = provider.refined_value(_Ore(), 250)  # 2.5 batches
        self.assertEqual(value, Decimal("25.0"))

    def test_zero_quantity_is_zero(self):
        provider = self._provider(_StubJanice({}))
        self.assertEqual(provider.refined_value(_Ore(), 0), Decimal("0"))

    def test_missing_recipe_fails_loud(self):
        janice = _StubJanice({})
        with mock.patch.object(pricing.EveTypeMaterial.objects, "filter", return_value=[]):
            provider = self._provider(janice)
            with self.assertRaises(JaniceError):
                provider.refined_value(_Ore(), 100)

    def test_missing_price_fails_loud(self):
        materials = [_Material(34, 10)]

        class _Empty:
            def prices(self, type_ids, *, basis):
                return {}

        with mock.patch.object(
            pricing.EveTypeMaterial.objects, "filter", return_value=materials
        ):
            provider = self._provider(_Empty())
            with self.assertRaises(JaniceError):
                provider.refined_value(_Ore(), 100)
