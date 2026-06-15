"""JaniceClient: request shape, basis selection, caching, fail-loud."""

from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from whatax import providers
from whatax.providers import JaniceClient, JaniceError


def _resp(status=200, payload=None):
    r = mock.Mock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else []
    return r


_PAYLOAD = [
    {
        "itemType": {"eid": 34, "volume": 0.01},
        "immediatePrices": {"buyPrice": 5.0, "splitPrice": 6.0, "sellPrice": 7.0},
        "top5AveragePrices": {"buyPrice": 5.5, "splitPrice": 6.5, "sellPrice": 7.5},
    }
]


class JaniceClientTest(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_split_immediate_basis(self):
        client = JaniceClient("KEY")
        with mock.patch.object(providers.requests, "post", return_value=_resp(payload=_PAYLOAD)) as post:
            prices = client.prices([34], basis="split_immediate")
        self.assertEqual(prices[34], Decimal("6.0"))
        # body is newline-joined type ids; auth header present
        _, kwargs = post.call_args
        self.assertEqual(kwargs["data"], "34")
        self.assertEqual(kwargs["headers"]["X-ApiKey"], "KEY")

    def test_buy_top5_basis(self):
        client = JaniceClient("KEY")
        with mock.patch.object(providers.requests, "post", return_value=_resp(payload=_PAYLOAD)):
            prices = client.prices([34], basis="buy_top5")
        self.assertEqual(prices[34], Decimal("5.5"))

    def test_cache_avoids_second_call(self):
        client = JaniceClient("KEY")
        with mock.patch.object(providers.requests, "post", return_value=_resp(payload=_PAYLOAD)) as post:
            client.prices([34], basis="split_immediate")
            client.prices([34], basis="split_immediate")
        self.assertEqual(post.call_count, 1)

    def test_unknown_basis_raises(self):
        with self.assertRaises(JaniceError):
            JaniceClient("KEY").prices([34], basis="nope")

    def test_missing_key_raises(self):
        with self.assertRaises(JaniceError):
            JaniceClient("").prices([34], basis="split_immediate")

    def test_http_error_raises(self):
        client = JaniceClient("KEY")
        with mock.patch.object(providers.requests, "post", return_value=_resp(status=500)):
            with self.assertRaises(JaniceError):
                client.prices([34], basis="split_immediate")
