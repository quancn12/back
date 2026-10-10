import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.run_evals import EVAL_ROOT, EvalContractError, _assert, _load_cases, _offline_probe, _run_browser, main as eval_main


class EvalSuiteContractTests(unittest.TestCase):
    def test_all_requested_suites_have_unique_executable_cases(self):
        expected = {
            "normal-chat",
            "itinerary-variable-length",
            "artifact-routing",
            "rag-format-leakage",
            "markdown-rendering",
            "responsive-layout",
            "loading-state",
            "context-and-controls",
        }
        files = sorted(EVAL_ROOT.glob("*.json"))
        suites = {json.loads(path.read_text(encoding="utf-8"))["suite"] for path in files}
        self.assertTrue(expected.issubset(suites))
        cases = _load_cases(None, None)
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(cases), 35)

    def test_offline_probes_execute_real_service_paths(self):
        cases = _load_cases({"artifact-routing", "itinerary-variable-length"}, None)
        for case in cases:
            if case["mode"] != "offline":
                continue
            result = _offline_probe(case)
            self.assertIsInstance(result, dict, case["id"])

    def test_generic_server_edit_probe_emits_period_patch_sse(self):
        case = next(case for case in _load_cases({"artifact-routing"}, None) if case["id"] == "generic-api-sse-period-patch")
        result = _offline_probe(case)
        self.assertEqual(result["status_code"], 200)
        self.assertTrue(result["has_action"])
        self.assertTrue(result["has_period_patch"])
        self.assertTrue(result["has_done"])

    def test_browser_cases_are_explicitly_capability_bound(self):
        cases = _load_cases({"markdown-rendering", "responsive-layout", "loading-state"}, None)
        self.assertTrue(cases)
        self.assertTrue(all(case["mode"] == "browser" for case in cases))
        self.assertTrue(all(case["probe"] for case in cases))

    def test_unknown_assertion_and_result_path_fail_clearly(self):
        with self.assertRaisesRegex(EvalContractError, "Unknown assertion op"):
            _assert({"value": 1}, {"op": "made_up", "path": "value"})
        with self.assertRaisesRegex(EvalContractError, "Unknown result path"):
            _assert({"value": 1}, {"op": "equals", "path": "missing", "value": 1})

    def test_unknown_and_empty_filters_are_errors(self):
        with self.assertRaisesRegex(EvalContractError, "Unknown case filter"):
            _load_cases(None, {"does-not-exist"})
        with self.assertRaisesRegex(EvalContractError, "matched no cases"):
            _load_cases({"markdown-rendering"}, {"normal-chat-greeting-is-deterministic"})

    def test_require_live_turns_skipped_live_case_into_nonzero(self):
        completed = subprocess.run(
            [sys.executable, "scripts/run_evals.py", "--suite", "normal-chat", "--mode", "live", "--require-live"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        report = json.loads(completed.stdout)
        self.assertEqual(report["summary"]["skipped"], 1)

    def test_browser_unavailable_is_skipped_but_require_browser_fails(self):
        case = _load_cases({"markdown-rendering"}, None)[0]
        with patch("scripts.run_evals.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "Puppeteer is not installed."
            report = _run_browser([case])
        self.assertEqual(report[0]["status"], "skipped")
        with patch("scripts.run_evals._run_browser", return_value=report), patch(
            "scripts.run_evals.sys.argv",
            ["run_evals.py", "--suite", "markdown-rendering", "--mode", "browser", "--require-browser"],
        ):
            self.assertEqual(eval_main(), 1)

class RequireFlagsCannotBeSilentTests(unittest.TestCase):
    """A gate that can never fail is worse than no gate at all.

    --mode defaults to "offline". Asking for --require-browser without widening
    the mode used to collect no browser cases, mark nothing skipped and exit 0,
    which reads in a CI file exactly like coverage that is not there.
    """

    def _run(self, argv):
        with patch("scripts.run_evals.sys.argv", ["run_evals.py", *argv]):
            return eval_main()

    def test_require_browser_with_default_mode_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            self._run(["--require-browser"])
        self.assertEqual(raised.exception.code, 2)

    def test_require_live_with_default_mode_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            self._run(["--require-live"])
        self.assertEqual(raised.exception.code, 2)

    def test_require_browser_is_accepted_once_the_mode_can_reach_it(self):
        for mode in ("browser", "all"):
            with self.subTest(mode=mode):
                report = [
                    {
                        "suite": "markdown-rendering",
                        "id": "x",
                        "mode": "browser",
                        "status": "skipped",
                        "reason": "Puppeteer is not installed.",
                    }
                ]
                with patch("scripts.run_evals._run_browser", return_value=report), patch(
                    "scripts.run_evals._run_offline", return_value=[]
                ), patch("scripts.run_evals._run_live", return_value=[]):
                    # Reaches the run instead of exiting 2, and the skip counts
                    # as the failure the flag exists to produce.
                    self.assertEqual(self._run(["--mode", mode, "--require-browser"]), 1)

    def test_the_error_names_the_flag_and_the_way_out(self):
        with patch("scripts.run_evals.sys.stderr.write") as write:
            with self.assertRaises(SystemExit):
                self._run(["--require-browser"])
        written = "".join(call.args[0] for call in write.call_args_list)
        self.assertIn("--require-browser", written)
        self.assertIn("--mode all", written)


if __name__ == "__main__":
    unittest.main()
