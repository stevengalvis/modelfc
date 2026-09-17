from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.generate_api_response_fixtures import generate


class ApiResponseFixtureTests(unittest.TestCase):
    def test_committed_fixtures_match_real_fastapi_responses(self):
        expected = Path(__file__).parent / "fixtures" / "api_v1"
        with TemporaryDirectory() as temporary:
            actual = Path(temporary)
            generate(actual)
            self.assertEqual(
                sorted(path.name for path in actual.iterdir()),
                sorted(path.name for path in expected.iterdir()),
            )
            for path in actual.iterdir():
                with self.subTest(path=path.name):
                    self.assertEqual(
                        path.read_text(encoding="utf-8"),
                        (expected / path.name).read_text(encoding="utf-8"),
                    )


if __name__ == "__main__":
    unittest.main()
