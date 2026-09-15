import ssl
import unittest
from urllib.request import urlopen

from scripts.run_webmainbench_benchmark import fixture_server


class FrozenFixtureTests(unittest.TestCase):
    def test_inline_query_navigation_keeps_the_same_frozen_page(self):
        frozen = "<h1>Frozen case</h1>"
        with fixture_server([{"track_id": "case-one", "html": frozen}]) as (base, ca_cert):
            context = ssl.create_default_context(cafile=str(ca_cert))
            with urlopen(base + "/case/case-one?nocdn=true", context=context, timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read().decode(), frozen)


if __name__ == "__main__":
    unittest.main()
