import unittest

from research.smc_core import (
    Bar,
    Direction,
    FVGStatus,
    InvalidTransition,
    SetupState,
    TradeDeal,
    atr_sma,
    consecutive_loss_count,
    confirmed_pivots,
    daily_loss_reached,
    detect_bos,
    detect_fvg,
    detect_sweep_reclaim,
    fvg_status,
    lifecycle_net_pnl,
    midpoint,
    net_reward_risk,
    normalize_volume,
    pending_expiry,
    qualifies_displacement,
    risk_loss_per_lot,
    spread_is_allowed,
    transition_setup,
    validate_stop_distance,
    valid_limit_entry,
)


def bar(time, open_, high, low, close, complete=True):
    return Bar(time, open_, high, low, close, complete)


class ConfirmedStructureTests(unittest.TestCase):
    def test_swing_requires_two_completed_right_bars(self):
        bars = [
            bar(1, 10, 11, 9, 10),
            bar(2, 10, 12, 9, 11),
            bar(3, 11, 15, 10, 14),
            bar(4, 14, 14, 10, 11),
            bar(5, 11, 11, 8, 9),
        ]
        highs, _ = confirmed_pivots(bars, span=2)
        self.assertEqual([(p.index, p.price, p.confirmation_time) for p in highs], [(2, 15, 5)])

        highs_before_confirmation, _ = confirmed_pivots(bars[:-1], span=2)
        self.assertEqual(highs_before_confirmation, [])

    def test_unfinished_h1_candle_cannot_confirm_swing_or_bos(self):
        bars = [
            bar(1, 10, 11, 9, 10),
            bar(2, 10, 12, 9, 11),
            bar(3, 11, 15, 10, 14),
            bar(4, 14, 14, 10, 11),
            bar(5, 11, 11, 8, 9, complete=False),
        ]
        self.assertEqual(confirmed_pivots(bars, span=2)[0], [])
        self.assertIsNone(detect_bos(bars, span=2))

    def test_bullish_bos_requires_completed_close_above_confirmed_swing(self):
        bars = [
            bar(1, 10, 11, 9, 10),
            bar(2, 10, 12, 9, 11),
            bar(3, 11, 15, 10, 14),
            bar(4, 14, 14, 10, 11),
            bar(5, 11, 11, 8, 9),
            bar(6, 9, 16, 9, 14),  # wick through 15; close remains below
            bar(7, 14, 16, 13, 15.1),
        ]
        event = detect_bos(bars, span=2)
        self.assertIsNotNone(event)
        self.assertEqual((event.direction, event.level, event.confirmation_time), (Direction.BUY, 15, 7))

    def test_bearish_bos_uses_close_and_neutralizes_on_opposing_break(self):
        bars = [
            bar(1, 10, 12, 9, 10),
            bar(2, 10, 11, 7, 8),
            bar(3, 8, 10, 5, 7),
            bar(4, 7, 9, 6, 7),
            bar(5, 7, 8, 6.5, 7),
            bar(6, 7, 8, 4, 5.5),  # wick through 5; close stays above
            bar(7, 5.5, 6, 3, 4.9),
        ]
        event = detect_bos(bars, span=2)
        self.assertIsNotNone(event)
        self.assertEqual((event.direction, event.confirmation_time), (Direction.SELL, 7))


class SweepAndDisplacementTests(unittest.TestCase):
    def test_ssl_sweep_reclaim_records_extreme_and_stable_identity(self):
        bars = [
            bar(100, 102, 103, 101, 102),
            bar(200, 102, 103, 98, 99),
            bar(300, 99, 102, 97, 101),
        ]
        first = detect_sweep_reclaim(bars, Direction.BUY, 100, 2, 0, "XAUUSD", max_level_age=100)
        second = detect_sweep_reclaim(bars, Direction.BUY, 100, 2, 0, "XAUUSD", max_level_age=100)
        self.assertIsNotNone(first)
        self.assertEqual((first.extreme, first.excursion_time, first.confirmation_time), (97, 200, 300))
        self.assertEqual(first.setup_id, second.setup_id)

    def test_sweep_without_reclaim_is_not_confirmed(self):
        bars = [bar(100, 102, 103, 97, 99), bar(200, 99, 100, 98, 99)]
        self.assertIsNone(detect_sweep_reclaim(bars, Direction.BUY, 100, 2, 0, "XAUUSD", max_level_age=100))

    def test_reclaim_without_threshold_sweep_is_rejected(self):
        bars = [bar(100, 100, 101, 99, 100.2)]
        self.assertIsNone(detect_sweep_reclaim(bars, Direction.BUY, 100, 2, 0, "XAUUSD", max_level_age=100))

    def test_old_liquidity_level_is_rejected(self):
        bars = [bar(i, 101, 102, 97, 101) for i in range(1, 7)]
        self.assertIsNone(detect_sweep_reclaim(bars, Direction.BUY, 100, 2, 0, "XAUUSD", max_level_age=1))

    def test_displacement_requires_body_ratio_and_close_location(self):
        self.assertTrue(qualifies_displacement(bar(1, 100, 103, 99, 102.5), 2.0, Direction.BUY))
        self.assertFalse(qualifies_displacement(bar(1, 100, 103, 99, 101), 2.0, Direction.BUY))
        self.assertFalse(qualifies_displacement(bar(1, 100, 100, 100, 100), 1.0, Direction.BUY))

    def test_m5_atr_is_sma_of_completed_true_ranges(self):
        bars = [
            bar(1, 10, 12, 9, 11),
            bar(2, 11, 14, 10, 13),
            bar(3, 13, 14, 12, 12.5),
        ]
        self.assertEqual(atr_sma(bars, period=2), 3.0)
        self.assertIsNone(atr_sma(bars + [bar(4, 12.5, 20, 12, 19, False)], period=2))


class FairValueGapTests(unittest.TestCase):
    def test_bullish_three_candle_gap_uses_c1_high_and_c3_low(self):
        gap = detect_fvg(
            [bar(10, 100, 102, 99, 101), bar(20, 101, 106, 100, 105), bar(30, 105, 108, 104, 107)],
            Direction.BUY,
            tick_size=0.1,
            sweep_time=5,
            displacement_time=20,
        )
        self.assertIsNotNone(gap)
        self.assertEqual((gap.lower, gap.upper, gap.created_time), (102, 104, 30))

    def test_bearish_three_candle_gap_uses_chronological_order(self):
        gap = detect_fvg(
            [bar(10, 106, 108, 105, 107), bar(20, 107, 108, 95, 96), bar(30, 96, 98, 94, 95)],
            Direction.SELL,
            tick_size=0.1,
            sweep_time=5,
            displacement_time=20,
        )
        self.assertIsNotNone(gap)
        self.assertEqual((gap.lower, gap.upper), (98, 105))

    def test_no_gap_or_gap_smaller_than_symbol_tick_is_rejected(self):
        no_gap = [bar(10, 100, 103, 99, 101), bar(20, 101, 106, 100, 105), bar(30, 105, 106, 100, 101)]
        self.assertIsNone(detect_fvg(no_gap, Direction.BUY, 0.1, 5, 20))
        sub_tick = [bar(10, 100, 102, 99, 101), bar(20, 101, 106, 100, 105), bar(30, 105, 108, 102.05, 107)]
        self.assertIsNone(detect_fvg(sub_tick, Direction.BUY, 0.1, 5, 20))

    def test_pre_sweep_or_unrelated_displacement_cannot_own_fvg(self):
        candles = [bar(10, 100, 102, 99, 101), bar(20, 101, 106, 100, 105), bar(30, 105, 108, 104, 107)]
        self.assertIsNone(detect_fvg(candles, Direction.BUY, 0.1, 25, 20))
        self.assertIsNone(detect_fvg(candles, Direction.BUY, 0.1, 5, 19))

    def test_unfinished_c3_is_not_a_valid_fvg(self):
        candles = [bar(10, 100, 102, 99, 101), bar(20, 101, 106, 100, 105), bar(30, 105, 108, 104, 107, False)]
        self.assertIsNone(detect_fvg(candles, Direction.BUY, 0.1, 5, 20))

    def test_fvg_lifecycle_distinguishes_fresh_partial_consumed_and_invalid(self):
        gap = detect_fvg(
            [bar(10, 100, 102, 99, 101), bar(20, 101, 106, 100, 105), bar(30, 105, 108, 104, 107)],
            Direction.BUY,
            0.1,
            5,
            20,
        )
        self.assertEqual(fvg_status(gap, []), FVGStatus.FRESH)
        self.assertEqual(fvg_status(gap, [bar(40, 107, 108, 103, 104)]), FVGStatus.PARTIALLY_MITIGATED)
        self.assertEqual(fvg_status(gap, [bar(40, 107, 108, 101, 103)]), FVGStatus.CONSUMED)
        self.assertEqual(fvg_status(gap, [bar(40, 107, 108, 101, 101)]), FVGStatus.INVALIDATED)


class StateRiskAndLedgerTests(unittest.TestCase):
    def test_setup_cannot_skip_sweep_or_reuse_terminal_state(self):
        self.assertEqual(transition_setup(SetupState.WAIT_BIAS, "bias_confirmed"), SetupState.WAIT_SWEEP)
        with self.assertRaises(InvalidTransition):
            transition_setup(SetupState.WAIT_BIAS, "sweep_confirmed")
        with self.assertRaises(InvalidTransition):
            transition_setup(SetupState.EXPIRED, "fvg_confirmed")

    def test_midpoint_and_pending_expiry_are_frozen(self):
        self.assertEqual(midpoint(1990.0, 1992.0), 1991.0)
        self.assertEqual(pending_expiry(1_000), 2_800)

    def test_volume_floors_to_step_rejects_below_min_and_caps_at_max(self):
        self.assertEqual(normalize_volume(100.0, 13.0, 0.01, 10.0, 0.01), 7.69)
        self.assertEqual(normalize_volume(1.0, 200.0, 0.01, 10.0, 0.01), 0.0)
        self.assertEqual(normalize_volume(1_000.0, 1.0, 0.01, 2.0, 0.01), 2.0)

    def test_invalid_tick_values_fail_closed(self):
        with self.assertRaises(ValueError):
            normalize_volume(100.0, 0.0, 0.01, 10.0, 0.01)
        with self.assertRaises(ValueError):
            normalize_volume(100.0, 10.0, 0.01, 10.0, 0.0)

    def test_ordercalcprofit_and_tick_properties_must_agree(self):
        self.assertEqual(risk_loss_per_lot(2000.0, 1999.0, 0.1, 1.0, 10.0), 10.0)
        with self.assertRaises(ValueError):
            risk_loss_per_lot(2000.0, 1999.0, 0.1, 1.0, 20.0)
        with self.assertRaises(ValueError):
            risk_loss_per_lot(2000.0, 1999.0, 0.0, 1.0, None)

    def test_spread_and_stop_distance_use_configured_symbol_units(self):
        self.assertTrue(spread_is_allowed(2000.0, 2000.5, 0.5))
        self.assertFalse(spread_is_allowed(2000.0, 2000.51, 0.5))
        self.assertTrue(validate_stop_distance(2000.0, 1999.0, 0.1, 10))
        self.assertFalse(validate_stop_distance(2000.0, 1999.0, 0.1, 11))

    def test_net_reward_risk_subtracts_costs_on_both_sides(self):
        self.assertAlmostEqual(net_reward_risk(30.0, 10.0, 1.0), 29.0 / 11.0)
        self.assertLess(net_reward_risk(30.0, 10.0, 2.0), 3.0)

    def test_daily_cap_uses_fixed_equity_reference_and_loss_streak_resets_on_nonloss(self):
        self.assertFalse(daily_loss_reached(10_000.0, 9_801.0, 2.0))
        self.assertTrue(daily_loss_reached(10_000.0, 9_800.0, 2.0))
        self.assertEqual(consecutive_loss_count([-1.0, -0.5]), 2)
        self.assertEqual(consecutive_loss_count([-1.0, 0.0, -0.2]), 1)

    def test_limit_order_must_be_on_correct_side_of_live_market(self):
        self.assertTrue(valid_limit_entry(Direction.BUY, 1999.0, 1999.1, 1999.2))
        self.assertFalse(valid_limit_entry(Direction.BUY, 1999.2, 1999.1, 1999.2))
        self.assertTrue(valid_limit_entry(Direction.SELL, 2001.0, 1999.1, 1999.2))
        self.assertFalse(valid_limit_entry(Direction.SELL, 1999.1, 1999.1, 1999.2))

    def test_partial_exit_deals_sum_actual_profit_and_all_cost_fields(self):
        deals = [
            TradeDeal(77, 9001, 1, 0.0, -2.0, 0.0, -1.0),
            TradeDeal(77, 9001, 2, 8.0, -0.5, -0.2, -0.1),
            TradeDeal(77, 9001, 3, -4.0, -0.5, -0.1, -0.1),
            TradeDeal(88, 9001, 4, 100.0, 0.0, 0.0, 0.0),
            TradeDeal(77, 42, 5, 1000.0, 0.0, 0.0, 0.0),
        ]
        self.assertAlmostEqual(lifecycle_net_pnl(deals, 77, 9001), -0.5)


if __name__ == "__main__":
    unittest.main()
