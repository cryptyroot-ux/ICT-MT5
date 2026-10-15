import csv
import tempfile
import unittest
from pathlib import Path

from research.experiment import (
    DataManifest,
    EvidenceError,
    FrozenConfig,
    Trade,
    aggregate_h1,
    block_bootstrap_expectancy,
    chronological_segments,
    compute_metrics,
    evaluate_gates,
    file_sha256,
    load_bars,
    purge_boundary_crossers,
    run_variant,
    validate_manifest,
    write_trade_csv,
)
from research.smc_core import Bar


def manifest(**overrides):
    base = dict(
        symbol="XAUUSD",
        broker="HFM Pro",
        account_server="HFMarketsGlobal-Demo",
        timezone="broker server time GMT+3 (verify per account)",
        start="2018-01-01",
        end="2026-10-01",
        data_type="real_ticks",
        source_kind="broker_export",
        sha256="0" * 64,
        spread_model="variable from broker ticks",
        commission_per_lot=7.0,
        swap_model="broker swap",
        slippage_per_lot=2.0,
        account_currency="USD",
        initial_equity=10000.0,
        tick_size=0.01,
        tick_value_loss=1.0,
        tick_value_profit=1.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        stops_level_points=0.0,
        freeze_level_points=0.0,
        point=0.01,
        digits=2,
        contract_size=100.0,
    )
    base.update(overrides)
    return DataManifest(**base)


class ManifestEvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "xauusd_m5.csv"
        self.data.write_text("time,open,high,low,close\n1,1,2,0.5,1.5\n", encoding="utf-8")
        self.digest = file_sha256(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_matching_hash_and_broker_source_is_accepted(self):
        validate_manifest(manifest(sha256=self.digest), self.data)

    def test_hash_mismatch_is_refused(self):
        with self.assertRaises(EvidenceError):
            validate_manifest(manifest(sha256="f" * 64), self.data)

    def test_synthetic_data_is_refused_for_oos(self):
        with self.assertRaises(EvidenceError):
            validate_manifest(manifest(sha256=self.digest, source_kind="synthetic"), self.data)

    def test_third_party_and_bar_model_are_refused_for_oos(self):
        with self.assertRaises(EvidenceError):
            validate_manifest(manifest(sha256=self.digest, source_kind="third_party"), self.data)
        with self.assertRaises(EvidenceError):
            validate_manifest(manifest(sha256=self.digest, data_type="bar_model"), self.data)

    def test_bar_model_is_allowed_for_exploratory_only(self):
        validate_manifest(manifest(sha256=self.digest, data_type="bar_model"), self.data, require_oos=False)

    def test_invalid_broker_properties_fail_closed(self):
        for bad in ({"tick_size": 0.0}, {"volume_step": 0.0}, {"tick_value_loss": -1.0},
                    {"volume_max": 0.001}, {"initial_equity": 0.0}, {"commission_per_lot": -1.0}):
            with self.subTest(bad=bad), self.assertRaises(EvidenceError):
                validate_manifest(manifest(sha256=self.digest, **bad), self.data)

    def test_non_xauusd_manifest_is_refused(self):
        with self.assertRaises(EvidenceError):
            validate_manifest(manifest(sha256=self.digest, symbol="EURUSD"), self.data)


class DataIntegrityTests(unittest.TestCase):
    def test_duplicate_or_unsorted_timestamps_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text(
                "time,open,high,low,close\n100,1,2,0.5,1.5\n100,1,2,0.5,1.4\n", encoding="utf-8"
            )
            with self.assertRaises(EvidenceError):
                load_bars(path)

    def test_h1_aggregation_ignores_incomplete_hours(self):
        # 12 closed M5 bars form one complete H1 bar; a 3-bar hour must be ignored.
        base = 3600 * 3600
        m5 = [Bar(base + i * 300, 2000, 2001 + i * 0.01, 1999, 2000.5) for i in range(12)]
        partial = [Bar(base + 3600 + i * 300, 2000, 2001, 1999, 2000.5) for i in range(3)]
        h1 = aggregate_h1(m5 + partial)
        self.assertEqual(len(h1), 1)
        self.assertEqual((h1[0].open, h1[0].high, h1[0].low, h1[0].close), (2000.0, 2001.11, 1999.0, 2000.5))
        self.assertEqual(h1[0].time, base)


class SegmentationTests(unittest.TestCase):
    def test_segments_are_chronological_and_disjoint(self):
        bars = [Bar(i * 3600, 1, 2, 0.5, 1.5) for i in range(100)]
        segments = chronological_segments(bars)
        self.assertEqual([s.name for s in segments], ["in_sample", "validation", "out_of_sample"])
        for earlier, later in zip(segments, segments[1:]):
            self.assertLess(earlier.end, later.start)

    def test_cross_boundary_trades_are_purged_from_both_segments(self):
        def trade(entry, exit_):
            return Trade("s", "B3", "BUY", entry, 1.0, 0.9, 1.3, 0.1, 10.0, exit_, "target", 30.0, 30.0, 0.0, 5)

        segments = [type("S", (), {"name": "in_sample", "start": 0, "end": 1000})(),
                    type("S", (), {"name": "out_of_sample", "start": 1001, "end": 2000})()]
        buckets = purge_boundary_crossers([trade(900, 1100), trade(1100, 1200), trade(100, 200)], segments)
        self.assertEqual(len(buckets["in_sample"]), 1)
        self.assertEqual(len(buckets["out_of_sample"]), 1)


def trade(pnl, risk=10.0, exit_time=0):
    return Trade("s", "B3", "BUY", 0, 1.0, 0.9, 1.3, 0.1, risk, exit_time, "target", pnl, pnl, 0.0, 5)


class MetricAndGateTests(unittest.TestCase):
    def test_metrics_are_derived_from_actual_trade_records(self):
        trades = [trade(30.0, 10.0, 1), trade(-10.0, 10.0, 2), trade(-10.0, 10.0, 3), trade(5.0, 10.0, 4)]
        metrics = compute_metrics(trades, equity_reference=1000.0, seed=7)
        self.assertEqual(metrics.trades, 4)
        self.assertAlmostEqual(metrics.win_rate, 50.0)
        self.assertAlmostEqual(metrics.expectancy_r, (3.0 - 1.0 - 1.0 + 0.5) / 4)
        self.assertAlmostEqual(metrics.profit_factor, 35.0 / 20.0)
        self.assertEqual(metrics.max_consecutive_losses, 2)
        self.assertGreater(metrics.max_drawdown_pct, 0.0)

    def test_empty_trade_set_yields_zeroed_metrics_not_a_pass(self):
        metrics = compute_metrics([], equity_reference=1000.0)
        self.assertEqual(metrics.trades, 0)

    def test_gate_is_inconclusive_below_one_hundred_trades_even_when_metrics_look_great(self):
        trades = [trade(60.0, 10.0, i) for i in range(99)]
        metrics = compute_metrics(trades, equity_reference=1000.0, seed=7)
        gates = {g.name: g for g in evaluate_gates(metrics)}
        self.assertEqual(gates["net_expectancy_r"].status, "INCONCLUSIVE")
        self.assertEqual(gates["profit_factor"].status, "INCONCLUSIVE")
        self.assertEqual(gates["completed_trades"].status, "INCONCLUSIVE")

    def test_gate_reports_fail_when_sample_is_sufficient_but_metrics_are_weak(self):
        # 48 small wins vs 72 larger losses: PF = 240/720 = 0.33, expectancy = -4.0R.
        trades = [trade(5.0, 10.0, i) for i in range(48)] + [trade(-10.0, 10.0, 100 + i) for i in range(72)]
        metrics = compute_metrics(trades, equity_reference=100000.0, seed=7)
        gates = {g.name: g for g in evaluate_gates(metrics)}
        self.assertEqual(gates["net_expectancy_r"].status, "FAIL")
        self.assertEqual(gates["profit_factor"].status, "FAIL")
        self.assertEqual(gates["completed_trades"].status, "PASS")

    def test_gate_reports_pass_only_when_all_thresholds_are_met(self):
        trades = [trade(40.0, 10.0, i) for i in range(120)]
        metrics = compute_metrics(trades, equity_reference=100000.0, seed=7)
        gates = {g.name: g for g in evaluate_gates(metrics)}
        self.assertEqual(gates["net_expectancy_r"].status, "PASS")
        self.assertEqual(gates["profit_factor"].status, "PASS")
        self.assertEqual(gates["max_drawdown_pct"].status, "PASS")
        self.assertEqual(gates["completed_trades"].status, "PASS")

    def test_expectancy_ci_is_absent_for_tiny_samples_and_present_otherwise(self):
        self.assertEqual(block_bootstrap_expectancy([1.0, -1.0], seed=1), (None, None))
        low, high = block_bootstrap_expectancy([1.0, -1.0] * 40, seed=1, resamples=200)
        self.assertIsNotNone(low)
        self.assertIsNotNone(high)
        self.assertLessEqual(low, high)

    def test_trade_csv_round_trips_actual_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trades.csv"
            write_trade_csv([trade(30.0, 10.0, 1)], path)
            with path.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(float(rows[0]["pnl"]), 30.0)
            self.assertEqual(float(rows[0]["r_multiple"]), 3.0)


class BaselineVariantTests(unittest.TestCase):
    def test_b0_cannot_be_produced_by_the_python_harness(self):
        with self.assertRaises(EvidenceError):
            run_variant([], [], "B0", manifest(), FrozenConfig(), 0, 1)

    def test_unknown_variant_is_refused(self):
        with self.assertRaises(EvidenceError):
            run_variant([], [], "B9", manifest(), FrozenConfig(), 0, 1)


if __name__ == "__main__":
    unittest.main()


class PnlUnitConsistencyTests(unittest.TestCase):
    """Regression: PnL must be account currency, matching the tick-value model used for risk."""

    def _bars(self):
        # Entry at bar 1 close (100.0); stop 99.00; target 103.00; bar 2 hits the stop.
        return [
            Bar(0, 100.0, 101.0, 99.5, 100.0),
            Bar(300, 100.0, 100.5, 99.8, 100.2),
            Bar(600, 100.0, 100.2, 98.5, 98.8),
        ]

    def _manifest(self, tick_value):
        return manifest(tick_size=0.01, tick_value_loss=tick_value, tick_value_profit=tick_value,
                        volume_min=0.01, volume_max=10.0, volume_step=0.01)

    def _run_one(self, tick_value):
        from research.experiment import Trade, _resolve_exits
        t = Trade("s", "B3", "BUY", 300, 100.2, 99.0, 103.0, 1.0, 120.0, 300, "open", 0.0, 360.0, 0.0, 0)
        return _resolve_exits(self._bars(), [t], 10, self._manifest(tick_value))[0]

    def test_pnl_is_not_a_bare_price_delta_times_volume(self):
        trade = self._run_one(17904.14)
        # A stop at -1.20 price units on 1.0 lot with 17904.14 IDR per 0.01 tick
        # must be about -1.20 * (17904.14/0.01) IDR, not -1.20 IDR.
        expected = -1.20 * (17904.14 / 0.01)
        self.assertAlmostEqual(trade.pnl, expected, places=6)
        self.assertNotAlmostEqual(trade.pnl, -1.20, places=3)

    def test_r_multiple_is_scale_invariant_and_pnl_scales_with_tick_value(self):
        # tick_value 1.00 per 0.01 tick -> 100 monetary units per price unit.
        low = self._run_one(1.0)
        self.assertAlmostEqual(low.pnl, -1.20 * 100.0, places=6)

        # tick_value 17904.14 per 0.01 tick -> 1_790_414 IDR per price unit.
        high = self._run_one(17904.14)
        self.assertAlmostEqual(high.pnl, -1.20 * (17904.14 / 0.01), places=3)
        self.assertAlmostEqual(high.pnl / low.pnl, (17904.14 / 0.01) / 100.0, places=4)

        # Same geometry and a proportionally scaled risk budget -> identical R multiple.
        low_t = self._run_one(1.0)
        low_t.risk_amount = 1.20 * 100.0
        high_t = self._run_one(17904.14)
        high_t.risk_amount = 1.20 * (17904.14 / 0.01)
        self.assertAlmostEqual(low_t.r_multiple, high_t.r_multiple, places=6)

    def test_timeout_exit_is_filled_at_market_not_zero(self):
        from research.experiment import Trade, _resolve_exits
        bars = [Bar(0, 100.0, 101.0, 99.5, 100.0), Bar(300, 100.0, 100.5, 99.9, 100.4)]
        t = Trade("s", "B3", "BUY", 300, 100.2, 90.0, 120.0, 1.0, 120.0, 300, "open", 0.0, 360.0, 0.0, 0)
        out = _resolve_exits(bars, [t], 5, self._manifest(1.0))[0]
        self.assertEqual(out.exit_reason, "timeout")
        # Close 100.4 vs entry 100.2 = +0.20 price units x 100.0 units/price unit.
        self.assertAlmostEqual(out.pnl, 0.20 * 100.0, places=6)
        self.assertNotEqual(out.pnl, 0.0)


class OrderLifetimeWindowTests(unittest.TestCase):
    """Regression: a pending order only ever sees bars inside its own lifetime."""

    def _bars(self):
        # 12 consecutive M5 bars starting at the reference time.
        return [Bar(300 + 300 * i, 100, 101, 99, 100) for i in range(12)]

    def test_window_stops_at_expiry_and_excludes_later_bars(self):
        from research.experiment import order_lifetime_bars

        cfg = FrozenConfig()
        bars = self._bars()
        window = order_lifetime_bars(bars, 0, 300, cfg)
        expiry = 300 + cfg.pending_expiry_seconds
        self.assertTrue(window)
        for bar in window:
            self.assertGreaterEqual(bar.time, 300)
            self.assertLess(bar.time, expiry)

    def test_a_bar_beyond_expiry_is_never_returned(self):
        from research.experiment import order_lifetime_bars

        cfg = FrozenConfig()
        bars = self._bars()
        expiry = 300 + cfg.pending_expiry_seconds
        far = [Bar(expiry + 3600 * i, 100, 101, 99, 100) for i in range(10)]
        self.assertEqual(order_lifetime_bars(far, 0, 300, cfg), [])

    def test_window_is_shorter_than_the_whole_series(self):
        from research.experiment import order_lifetime_bars

        cfg = FrozenConfig()
        bars = self._bars()
        self.assertLess(len(order_lifetime_bars(bars, 0, 300, cfg)), len(bars))
