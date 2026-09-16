"""Configured history must run the same models as explicit file arguments."""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from modelfc import corner_evaluation, corner_predict


class ConfiguredCornerCliTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.history = self.root / "SP1_2627.csv"
        self.history.write_text(
            "Div,Date,HomeTeam,AwayTeam,HC,AC\n"
            "SP1,01/09/2026,A,B,2,3\n"
            "SP1,02/09/2026,A,B,5,1\n"
            "SP1,03/09/2026,A,B,3,5\n"
            "SP1,04/09/2026,A,B,6,2\n"
        )
        self.config = self.root / "corner_data.json"
        self.config.write_text(json.dumps({"data_directory": ".", "leagues": ["SP1"], "max_age_days": 14}))
        self.config_args = ["--data-config", str(self.config), "--competition", "SP1"]

    def run_cli(self, module, arguments):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(sys, "argv", ["command"] + arguments), redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                module.main()
            except SystemExit as error:
                return error.code, stdout.getvalue(), stderr.getvalue()
        return 0, stdout.getvalue(), stderr.getvalue()

    def test_evaluation_matches_explicit_history_exactly(self):
        options = ["--model", "venue-opponent-negative-binomial", "--min-history", "2"]
        explicit = self.run_cli(corner_evaluation, [str(self.history)] + options)
        configured = self.run_cli(corner_evaluation, self.config_args + options)
        self.assertEqual(explicit[0], 0)
        self.assertEqual(configured, explicit)

    def test_fixture_matches_explicit_history_and_warns_on_old_data(self):
        options = ["--date", "2026-10-01", "--home", "A", "--away", "B", "--home-lines", "3.5", "--min-history", "2", "--min-venue-history", "1"]
        explicit = self.run_cli(corner_predict, ["--history", str(self.history)] + options)
        configured = self.run_cli(corner_predict, self.config_args + options)
        self.assertEqual(configured[0], 0)
        self.assertEqual(configured[1].replace("Competition: SP1\n", ""), explicit[1])
        self.assertIn("WARNING: latest usable history", configured[1])
        self.assertIn("Line 3.5: OVER=", configured[1])

    def test_conflicting_provider_and_file_selectors_are_rejected(self):
        for module in (corner_evaluation, corner_predict):
            required = [] if module is corner_evaluation else ["--date", "2026-10-01", "--home", "A", "--away", "B"]
            for extra in (["--provider", "mls"], ["--country", "Spain"], ["--league", "LaLiga"]):
                with self.subTest(module=module.__name__, extra=extra):
                    result = self.run_cli(module, self.config_args + required + extra)
                    self.assertEqual(result[0], 2)
            path_args = [str(self.history)] if module is corner_evaluation else ["--history", str(self.history)]
            self.assertEqual(self.run_cli(module, self.config_args + required + path_args)[0], 2)
            self.assertEqual(self.run_cli(module, ["--data-config", str(self.config)] + required)[0], 2)


if __name__ == "__main__":
    unittest.main()
