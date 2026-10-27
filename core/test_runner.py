"""A test runner that keeps push delivery off the network.

`EXPO_ACCESS_TOKEN` comes from `.env`, so without this guard any test that puts a token on a user
would really call exp.host. Tests that want to assert on a delivery patch the transport and set a
stand-in token for the block they run in.
"""

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class PushFreeTestRunner(DiscoverRunner):
    def run_tests(self, *args, **kwargs):
        with override_settings(EXPO_ACCESS_TOKEN=''):
            return super().run_tests(*args, **kwargs)
