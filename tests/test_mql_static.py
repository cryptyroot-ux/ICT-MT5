"""Static structural checks for the SMC MQL5 sources.

These are NOT MetaEditor compile tests and prove nothing about runtime behavior.
MetaEditor/Strategy Tester remain separate gates (see docs/VALIDATION_REPORT.md).
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SMC_FILES = sorted((REPO / "Include").glob("SMC_*.mqh")) + sorted((REPO / "Experts").glob("SMC_*.mq5"))


def strip_literals(text):
    """Remove comments and string literals so brace counting is meaningful."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
        elif ch == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
            i += 1
            out.append('""')
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class MqlStaticStructureTests(unittest.TestCase):
    def test_expected_smc_sources_exist(self):
        expected = {
            "Experts/SMC_H1M5_SweepFVG.mq5",
            "Include/SMC_Config.mqh",
            "Include/SMC_MarketStructure.mqh",
            "Include/SMC_Liquidity.mqh",
            "Include/SMC_Displacement.mqh",
            "Include/SMC_FVG.mqh",
            "Include/SMC_SetupStateMachine.mqh",
            "Include/SMC_RiskManager.mqh",
            "Include/SMC_Execution.mqh",
            "Include/SMC_TradeLedger.mqh",
            "Include/SMC_Logger.mqh",
        }
        present = {str(p.relative_to(REPO)) for p in SMC_FILES}
        self.assertEqual(expected - present, set())

    def test_braces_and_parentheses_are_balanced(self):
        for path in SMC_FILES:
            code = strip_literals(path.read_text(encoding="utf-8"))
            with self.subTest(file=path.name):
                self.assertEqual(code.count("{"), code.count("}"))
                self.assertEqual(code.count("("), code.count(")"))

    def test_every_smc_include_resolves_to_a_file(self):
        for path in SMC_FILES:
            for include in re.findall(r'#include\s+<(SMC_[^>]+)>', path.read_text(encoding="utf-8")):
                with self.subTest(file=path.name, include=include):
                    self.assertTrue((REPO / "Include" / include).is_file())

    def test_no_stray_diff_markers_or_escaped_quote_literals(self):
        for path in SMC_FILES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertNotRegex(text, r"(?m)^\+")
                self.assertNotRegex(text, r'(?<!\\)"XAU"')

    def test_guards_are_paired_on_every_include(self):
        for path in SMC_FILES:
            if path.suffix == ".mq5":
                # The EA is an entry point, not an include, so it needs no guard.
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"#ifndef\s+SMC_")
                continue
            text = path.read_text(encoding="utf-8")
            ifndef = re.findall(r"^#ifndef\s+(\w+)", text, re.M)
            define = re.findall(r"^#define\s+(\w+)", text, re.M)
            with self.subTest(file=path.name):
                self.assertEqual(len(ifndef), 1)
                self.assertIn(ifndef[0], define)
                self.assertEqual(text.count("#ifndef"), text.count("#endif"))

    def test_logger_signature_matches_every_call_site(self):
        signature = (REPO / "Include" / "SMC_Logger.mqh").read_text(encoding="utf-8")
        declared = re.search(r"void Write\((.*?)\) const", signature, re.S)
        self.assertIsNotNone(declared)
        params = [p.strip() for p in declared.group(1).split(",")]
        self.assertEqual(params[0], "const SMC_SETUP &setup")
        self.assertEqual(params[1], "const string event")
        self.assertEqual(params[2], "const string state_before")
        self.assertEqual(params[3], "const double spread")

        # Required argument count = 7 positional + up to 5 defaulted trailing params.
        required = len([p for p in params if "=" not in p])
        callers = [p for p in SMC_FILES if p.name != "SMC_Logger.mqh"]
        for path in callers:
            for match in re.finditer(r"\.Write\((.*?)\);", path.read_text(encoding="utf-8"), re.S):
                args = split_args(match.group(1))
                with self.subTest(file=path.name):
                    self.assertGreaterEqual(len(args), required)
                    self.assertLessEqual(len(args), len(params))

    def test_no_mt4_only_trade_api_is_used(self):
        forbidden = ["OrderSend(", "OrderSelect("]
        allowed_by_module = {"SMC_Execution.mqh"}
        for path in SMC_FILES:
            if path.name in allowed_by_module:
                continue
            text = path.read_text(encoding="utf-8")
            for api in forbidden:
                with self.subTest(file=path.name, api=api):
                    self.assertNotIn(api, text)

    def test_setup_order_position_identifiers_are_distinct_fields(self):
        config = (REPO / "Include" / "SMC_Config.mqh").read_text(encoding="utf-8")
        struct = re.search(r"struct SMC_SETUP\s*\{(.*?)\n  \};", config, re.S)
        self.assertIsNotNone(struct)
        body = struct.group(1)
        # setup_id is textual; broker identifiers are numeric and must not alias one another.
        for field, type_name in (
            ("setup_id", "string"),
            ("order_ticket", "ulong"),
            ("deal_id", "ulong"),
            ("position_id", "ulong"),
        ):
            with self.subTest(field=field):
                self.assertRegex(body, rf"\b{type_name}\s+{field};")


def split_args(text):
    args, depth, current = [], 0, ""
    in_string = False
    for ch in text:
        if ch == '"':
            in_string = not in_string
        if not in_string:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                args.append(current)
                current = ""
                continue
        current += ch
    if current.strip():
        args.append(current)
    return [a.strip() for a in args if a.strip()]


if __name__ == "__main__":
    unittest.main()


class StaleInstructionGuardTests(unittest.TestCase):
    """Agent-facing files must keep their superseded markers.

    These files drive automated revisions; an unmarked stale rule (e.g. "1% risk
    per trade") can silently override the frozen specification on a later change.
    Each anchor is a distinctive phrase so that deleting the marker actually fails,
    rather than passing on some unrelated occurrence of the word.
    """

    ANCHORS = {
        "AGENTS.md": "PARTIALLY SUPERSEDED",
        "PROJECT_PLAN.md": "SUPERSEDED — historical planning record only",
        "CONTRIBUTING.md": "Scope note",
        "TEST_REPORT.md": "LEGACY BASELINE — not the SMC strategy",
        "README.md": "LEGACY BASELINE ONLY",
    }

    def test_superseded_markers_are_present(self):
        for name, anchor in self.ANCHORS.items():
            with self.subTest(file=name):
                self.assertIn(
                    anchor,
                    (REPO / name).read_text(encoding="utf-8"),
                    f"{name} lost its superseded marker; stale rules could be followed again",
                )

    def test_active_routing_map_states_the_frozen_risk_range(self):
        text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("0.25", text)
        self.assertIn("0.50", text)
        self.assertIn("SMC_H1M5_SWEEPFVG_SPEC.md", text)

    def test_frozen_thresholds_are_not_restated_differently_in_active_docs(self):
        # Any doc that mentions the expectancy threshold must also state the
        # non-negotiable no-relaxation rule, so a future edit cannot lower it quietly.
        for name in ("AGENTS.md", "CONTRIBUTING.md"):
            text = (REPO / name).read_text(encoding="utf-8")
            if "+0.25R" in text or "0.25R" in text:
                with self.subTest(file=name):
                    self.assertRegex(text, r"never be relaxed|must not be relaxed|never be averaged")


class LiquidityGuardParityTests(unittest.TestCase):
    """Structural parity: the MQL5 must carry the same two guards as the model.

    These are static presence checks, not behaviour tests. MQL5 cannot be executed
    here, so parity is asserted structurally and labelled as such in the docs.
    """

    LIQ = REPO / "Include" / "SMC_Liquidity.mqh"
    EA = REPO / "Experts" / "SMC_H1M5_SweepFVG.mq5"

    def test_sweep_detection_has_an_explicit_upper_bound_to_server_now(self):
        text = self.LIQ.read_text(encoding="utf-8")
        self.assertIn("SMC_SETUP_DEVELOPMENT_BARS", text)
        self.assertIn("TimeTradeServer", text)
        self.assertIn("now-(SMC_SETUP_DEVELOPMENT_BARS*timeframe_seconds)", text)

    def test_sweep_detection_prefers_the_newest_live_confirmation(self):
        text = self.LIQ.read_text(encoding="utf-8")
        self.assertIn("confirmation_time>best_confirmation", text)

    def test_target_lookup_rejects_already_swept_levels(self):
        text = self.LIQ.read_text(encoding="utf-8")
        # The guard must be a named helper AND must actually be consulted by the
        # target lookup; asserting only the identifier would survive its removal.
        self.assertIn("bool LevelStillResting(", text)
        self.assertIn("if(!LevelStillResting(rates,confirmation_index,closed_end,candidate,direction))",
                      text)
        # The helper must scan bars from the pivot confirmation to the last closed bar.
        self.assertRegex(text, r"for\(int probe=confirmation_index;probe<=closed_end;probe\+\+\)")
        self.assertIn("rates[probe].high>=level", text)
        self.assertIn("rates[probe].low<=level", text)

    def test_bos_is_consumed_only_after_a_sweep_is_confirmed(self):
        text = self.EA.read_text(encoding="utf-8")
        bias_at = text.index('if(g_smc_setup.state==SMC_WAIT_BIAS)')
        sweep_at = text.index('if(g_smc_setup.state==SMC_WAIT_SWEEP)')
        consume_calls = [m.start() for m in re.finditer(r"SaveConsumedBias\(", text)]
        # One definition and exactly one call site, and the call must sit in the
        # WAIT_SWEEP branch, after the sweep is confirmed.
        self.assertEqual(len(consume_calls), 2, "expected one definition and one call")
        self.assertGreater(consume_calls[1], sweep_at)
        self.assertNotIn("SaveConsumedBias(", text[bias_at:sweep_at])

    def test_stale_bias_resets_so_a_newer_bos_can_seed(self):
        text = self.EA.read_text(encoding="utf-8")
        self.assertIn("bias_stale_reset", text)
        self.assertRegex(text, r"h1_bos_confirmation\+SMC_SETUP_DEVELOPMENT_BARS")
