"""Tests for the experimental V2 causal reference model.

V2 is a research fixture, not the MQL5 EA and not a broker-accurate backtest.
These tests lock the causal contract: only data available at the decision
timestamp may influence a decision, session windows use closed bars only, and
order submission stays gated until transaction costs are verified.
"""

import calendar
import csv
import datetime
import json
import tempfile
import unittest
from pathlib import Path

from research.smc_core import Bar, Direction, FairValueGap, LiquiditySweep, StructureBreak
from research.v2_parity import (
    CostModel,
    DailyRange,
    Trade,
    ServerBar,
    V2Config,
    V2Setup,
    aggregate_daily_ranges,
    bos_close_confirmation,
    build_v2_candidates,
    enumerate_causal_sweeps,
    find_post_sweep_displacement,
    latest_bos_before,
    load_v2_bars,
    may_submit_order,
    pending_limit_fill,
    premium_discount_allows,
    prior_daily_range,
    protection_levels,
    main,
    rolling_session_allowed,
    run_v2_exploratory,
    simulate_v2_pending,
    summarise_trades,
    within_development_window,
)


def make_bar(stamp, high, low, close, spread_price=0.30):
    return ServerBar(
        time=calendar.timegm(stamp.timetuple()),
        server_day=stamp.date(),
        server_hour=stamp.hour,
        open=close,
        high=high,
        low=low,
        close=close,
        spread_price=spread_price,
        complete=True,
    )


def range_bar(index, day, hour, high, low):
    return ServerBar(
        index * 300, day, hour, low + (high - low) / 2.0, high, low,
        low + (high - low) / 2.0, 0.30,
    )


class PremiumDiscountCausalityTests(unittest.TestCase):
    def test_pd_uses_previous_completed_day_not_current_day_extremes(self):
        jan1 = datetime.datetime(2020, 1, 1, 23, 55)
        jan2a = datetime.datetime(2020, 1, 2, 10, 0)
        jan2b = datetime.datetime(2020, 1, 2, 15, 0)
        before = [make_bar(jan1, 120, 80, 100), make_bar(jan2a, 100, 90, 95)]
        after = before + [make_bar(jan2b, 150, 80, 120)]
        previous_before = prior_daily_range(aggregate_daily_ranges(before), jan2a.date())
        previous_after = prior_daily_range(aggregate_daily_ranges(after), jan2a.date())
        self.assertEqual(previous_before, previous_after)
        self.assertEqual(previous_before.day, datetime.date(2020, 1, 1))
        self.assertTrue(premium_discount_allows(Direction.BUY, 95.0, previous_before))
        same_day_before = DailyRange(datetime.date(2020, 1, 2), 100.0, 90.0)
        same_day_after = DailyRange(datetime.date(2020, 1, 2), 150.0, 80.0)
        self.assertFalse(premium_discount_allows(Direction.BUY, 95.0, same_day_before))
        self.assertTrue(premium_discount_allows(Direction.BUY, 95.0, same_day_after))

    def test_current_day_range_is_not_a_valid_prior_range(self):
        first = datetime.datetime(2020, 1, 1, 12, 0)
        second = datetime.datetime(2020, 1, 2, 12, 0)
        bars = [make_bar(first, 110, 90, 100), make_bar(second, 130, 80, 100)]
        daily = aggregate_daily_ranges(bars)
        chosen = prior_daily_range(daily, datetime.date(2020, 1, 2))
        self.assertEqual(chosen.day, datetime.date(2020, 1, 1))

    def test_incomplete_bars_are_excluded_from_daily_ranges(self):
        day = datetime.date(2020, 1, 1)
        complete = ServerBar(0, day, 10, 100, 110, 90, 105, 0.30)
        forming = ServerBar(300, day, 11, 100, 500, 1, 300, 0.30, complete=False)
        daily = aggregate_daily_ranges([complete, forming])
        self.assertEqual(daily, [DailyRange(day, 110.0, 90.0)])

    def test_pd_rejects_missing_or_degenerate_prior_range(self):
        day = datetime.date(2020, 1, 1)
        self.assertFalse(premium_discount_allows(Direction.BUY, 95.0, None))
        degenerate = DailyRange(day, 100.0, 100.0)
        self.assertFalse(premium_discount_allows(Direction.BUY, 95.0, degenerate))
        self.assertFalse(premium_discount_allows(Direction.SELL, 105.0, degenerate))


class SessionWindowTests(unittest.TestCase):
    def test_rolling_session_uses_only_bars_before_decision(self):
        day = datetime.date(2020, 1, 1)
        history = [ServerBar(i * 300, day, 9, 100, 101, 99, 100, 0.30) for i in range(200)]
        decision = ServerBar(200 * 300, day, 10, 100, 200, 50, 100, 0.30)
        self.assertFalse(rolling_session_allowed(history + [decision], decision_index=200))

    def test_rolling_session_is_limited_to_the_last_200_closed_bars(self):
        day = datetime.date(2020, 1, 1)
        history = [range_bar(0, day, 9, 120.0, 20.0)]
        history += [range_bar(i + 1, day, 10, 110.0, 100.0) for i in range(100)]
        history += [range_bar(i + 101, day, 11, 102.0, 100.0) for i in range(100)]
        decision = range_bar(201, day, 10, 110.0, 100.0)
        bars = history + [decision]
        # Including the old high-range hour 9 bar would reject this decision, so a
        # True result proves the window really is the last 200 bars.
        self.assertTrue(rolling_session_allowed(bars, decision_index=201))
        self.assertTrue(
            rolling_session_allowed(bars, decision_index=201, lookback_bars=400)
            is False
        )

    def test_session_rejects_hours_without_history(self):
        day = datetime.date(2020, 1, 1)
        history = [range_bar(i, day, 9, 110.0, 100.0) for i in range(100)]
        history += [range_bar(100 + i, day, 10, 102.0, 100.0) for i in range(100)]
        decision = range_bar(200, day, 14, 110.0, 100.0)
        self.assertFalse(rolling_session_allowed(history + [decision], decision_index=200))

    def test_session_requires_full_window_of_closed_bars(self):
        day = datetime.date(2020, 1, 1)
        history = [range_bar(i, day, 9, 110.0, 100.0) for i in range(5)]
        decision = range_bar(5, day, 9, 110.0, 100.0)
        self.assertFalse(rolling_session_allowed(history + [decision], decision_index=5))

    def test_session_matches_mql_threshold_arithmetic(self):
        day = datetime.date(2020, 1, 1)
        history = [range_bar(i, day, 10, 120.0, 100.0) for i in range(100)]
        history += [range_bar(100 + i, day, 11, 105.0, 100.0) for i in range(100)]
        decision = range_bar(200, day, 10, 120.0, 100.0)
        bars = history + [decision]
        # hour 10 mean 20, hour 11 mean 5, mean of means 12.5, threshold 16.25.
        self.assertTrue(rolling_session_allowed(bars, decision_index=200, ratio=1.30))
        self.assertFalse(rolling_session_allowed(bars, decision_index=200, ratio=2.00))


class BarLoadingTests(unittest.TestCase):
    def _write_csv(self, rows):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        )
        writer = csv.writer(handle)
        writer.writerow(
            ["datetime", "open", "high", "low", "close", "tick_volume", "spread_points"]
        )
        for row in rows:
            writer.writerow(row)
        handle.close()
        return Path(handle.name)

    def test_loader_stops_at_end_time_so_burned_oos_is_never_read(self):
        path = self._write_csv(
            [
                ["2020.01.02 10:00:00", 100, 101, 99, 100, 5, 23],
                ["2020.01.02 10:05:00", 100, 102, 99, 101, 5, 24],
                ["2024.04.23 18:10:00", 200, 201, 199, 200, 5, 25],
            ]
        )
        end = datetime.datetime(2024, 4, 23, 18, 5)
        bars = load_v2_bars(path, end_time=end)
        self.assertEqual([bar.time for bar in bars], [
            calendar.timegm(datetime.datetime(2020, 1, 2, 10, 0).timetuple()),
            calendar.timegm(datetime.datetime(2020, 1, 2, 10, 5).timetuple()),
        ])
        self.assertEqual([bar.spread_price for bar in bars], [0.23, 0.24])
        self.assertEqual(bars[0].server_day, datetime.date(2020, 1, 2))
        self.assertEqual(bars[0].server_hour, 10)

    def test_loader_skips_rows_before_start_time(self):
        path = self._write_csv(
            [
                ["2018.06.29 23:55:00", 100, 101, 99, 100, 5, 23],
                ["2018.07.02 00:00:00", 100, 102, 99, 101, 5, 24],
            ]
        )
        start = datetime.datetime(2018, 7, 1)
        bars = load_v2_bars(path, start_time=start)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].server_day, datetime.date(2018, 7, 2))

    def test_loader_rejects_non_increasing_timestamps(self):
        path = self._write_csv(
            [
                ["2020.01.02 10:05:00", 100, 102, 99, 101, 5, 23],
                ["2020.01.02 10:00:00", 100, 101, 99, 100, 5, 23],
            ]
        )
        with self.assertRaises(ValueError):
            load_v2_bars(path)

    def test_loader_rejects_empty_input(self):
        path = self._write_csv([])
        with self.assertRaises(ValueError):
            load_v2_bars(path)


class FrozenConfigTests(unittest.TestCase):
    def test_v2_config_defaults_match_preregistered_constants(self):
        config = V2Config()
        self.assertEqual(config.min_sl_price, 20.0)
        self.assertEqual(config.atr_sl_multiplier, 3.0)
        self.assertEqual(config.session_ratio, 1.30)
        self.assertEqual(config.target_r, 3.0)
        self.assertEqual(config.session_lookback_bars, 200)
        self.assertEqual(config.pending_expiry_bars, 6)
        self.assertEqual(config.pending_expiry_seconds, 1800)
        self.assertEqual(config.max_spread_price, 0.50)
        self.assertEqual(config.risk_percent, 0.25)
        self.assertEqual(config.daily_loss_percent, 2.0)
        self.assertTrue(config.observe_only)

    def test_config_is_immutable_so_thresholds_cannot_drift(self):
        config = V2Config()
        with self.assertRaises(Exception):
            config.target_r = 9.0


class OrderGateTests(unittest.TestCase):
    def test_order_submission_requires_verified_costs_and_live_mode(self):
        self.assertTrue(may_submit_order(cost_verified=True, observe_only=False))
        self.assertFalse(may_submit_order(cost_verified=False, observe_only=False))
        self.assertFalse(may_submit_order(cost_verified=True, observe_only=True))
        self.assertFalse(may_submit_order(cost_verified=False, observe_only=True))


FIXTURE_START = datetime.datetime(2020, 1, 1, 0, 0)
DAY1_BARS = 288  # one full server day of M5 bars


def stamp_for(index, period_minutes=5):
    return FIXTURE_START + datetime.timedelta(minutes=period_minutes * index)


def make_indexed_bar(index, high, low, close, open_price=None, period_minutes=5):
    stamp = stamp_for(index, period_minutes)
    return ServerBar(
        time=calendar.timegm(stamp.timetuple()),
        server_day=stamp.date(),
        server_hour=stamp.hour,
        open=close if open_price is None else open_price,
        high=high,
        low=low,
        close=close,
        spread_price=0.30,
        complete=True,
    )


def make_buy_setup_fixture():
    """M5 fixture carrying a complete causal BUY setup after a fresh H1 BOS.

    Layout: 288 flat day-1 bars (one of them wide so the prior-day midpoint
    sits above the entry), flat day-2 filler, then a wide-range hour containing
    the pool low, its reclaim, an internal pivot high, the displacement, the
    FVG, and the decision bar.
    """
    bars = []
    for index in range(502):
        if index < DAY1_BARS:
            bars.append(make_indexed_bar(index, 2001.0, 1999.0, 2000.0))
        else:
            bars.append(make_indexed_bar(index, 2001.0, 1999.0, 2000.0))
    # Widen one day-1 bar so the prior-day midpoint (2049.5) is above the entry.
    bars[100] = make_indexed_bar(100, 2100.0, 1999.0, 2000.0)

    # Wide-range hour: pool low, excursion, reclaim, pivot high, displacement, FVG.
    bars[488] = make_indexed_bar(488, 2022.0, 1998.0, 2000.0, 2000.0)
    bars[489] = make_indexed_bar(489, 2002.0, 1990.0, 1991.0, 2001.0)  # pool pivot low
    bars[490] = make_indexed_bar(490, 2005.0, 1995.0, 2004.0, 1996.0)
    bars[491] = make_indexed_bar(491, 2009.0, 1997.0, 2008.0, 2004.0)
    bars[492] = make_indexed_bar(492, 2009.0, 1998.0, 2001.0, 2008.0)
    bars[493] = make_indexed_bar(493, 2002.0, 1989.0, 1989.5, 2001.0)  # excursion
    bars[494] = make_indexed_bar(494, 2005.0, 1989.0, 2004.0, 1990.0)  # reclaim
    bars[495] = make_indexed_bar(495, 2005.0, 1993.0, 2004.0, 2004.0)
    bars[496] = make_indexed_bar(496, 2020.0, 1996.0, 2015.0, 2004.0)  # internal pivot high
    bars[497] = make_indexed_bar(497, 2016.0, 2005.0, 2010.0, 2015.0)
    bars[498] = make_indexed_bar(498, 2012.0, 2008.0, 2010.0, 2010.0)
    bars[499] = make_indexed_bar(499, 2035.0, 2007.0, 2032.0, 2009.0)  # displacement
    bars[500] = make_indexed_bar(500, 2036.0, 2014.0, 2033.0, 2030.0)  # FVG C3
    bars[501] = make_indexed_bar(501, 2036.0, 2030.0, 2034.0, 2033.0)  # decision bar
    return bars


def make_buy_bias_h1_fixture():
    """H1 bars whose confirmed close breaks a pivot high inside the dev window."""
    bars = []
    for index in range(42):
        bars.append(make_indexed_bar(index, 2000.0, 1990.0, 1995.0, 1995.0, 60))
    bars[37] = make_indexed_bar(37, 2020.0, 1990.0, 2010.0, 1995.0, 60)  # pivot high
    bars[40] = make_indexed_bar(40, 2026.0, 2020.0, 2025.0, 2021.0, 60)  # BUY BOS
    bars[41] = make_indexed_bar(41, 2026.0, 2020.0, 2025.0, 2025.0, 60)
    return bars


def make_sell_setup_fixture():
    """Mirror image of the BUY fixture: pool high swept upward, then reclaimed."""
    bars = []
    for index in range(502):
        bars.append(make_indexed_bar(index, 2001.0, 1999.0, 2000.0))
    # Widen one day-1 bar downward so the prior-day midpoint sits below the entry.
    bars[100] = make_indexed_bar(100, 2001.0, 1900.0, 2000.0)

    bars[488] = make_indexed_bar(488, 2002.0, 1978.0, 2000.0, 2000.0)
    bars[489] = make_indexed_bar(489, 2010.0, 1998.0, 2009.0, 1999.0)  # pool pivot high
    bars[490] = make_indexed_bar(490, 2005.0, 1995.0, 1996.0, 2004.0)
    bars[491] = make_indexed_bar(491, 2003.0, 1991.0, 1992.0, 1996.0)
    bars[492] = make_indexed_bar(492, 1999.0, 1991.0, 1999.0, 1992.0)
    bars[493] = make_indexed_bar(493, 2011.0, 1998.0, 2010.5, 1999.0)  # excursion
    bars[494] = make_indexed_bar(494, 2011.0, 1995.0, 1996.0, 2010.5)  # reclaim
    bars[495] = make_indexed_bar(495, 2007.0, 1995.0, 1996.0, 1996.0)
    bars[496] = make_indexed_bar(496, 2004.0, 1980.0, 1985.0, 1996.0)  # internal pivot low
    bars[497] = make_indexed_bar(497, 1995.0, 1984.0, 1990.0, 1985.0)
    bars[498] = make_indexed_bar(498, 1992.0, 1988.0, 1990.0, 1990.0)
    bars[499] = make_indexed_bar(499, 1993.0, 1965.0, 1968.0, 1991.0)  # displacement
    bars[500] = make_indexed_bar(500, 1986.0, 1964.0, 1967.0, 1970.0)  # FVG C3
    bars[501] = make_indexed_bar(501, 1970.0, 1964.0, 1966.0, 1966.0)  # decision bar
    return bars


def make_sell_bias_h1_fixture():
    """H1 bars whose confirmed close breaks a pivot low inside the dev window."""
    bars = []
    for index in range(42):
        bars.append(make_indexed_bar(index, 2000.0, 1990.0, 1995.0, 1995.0, 60))
    bars[37] = make_indexed_bar(37, 2000.0, 1970.0, 1980.0, 1995.0, 60)  # pivot low
    bars[40] = make_indexed_bar(40, 1980.0, 1960.0, 1965.0, 1979.0, 60)  # SELL BOS
    bars[41] = make_indexed_bar(41, 1980.0, 1974.0, 1975.0, 1975.0, 60)
    return bars


class PendingOrderParityTests(unittest.TestCase):
    def test_pending_order_is_not_active_before_fvg_confirmation(self):
        c1 = Bar(300, 100.0, 101.0, 99.0, 100.5)
        c2 = Bar(600, 100.5, 102.0, 100.0, 101.8)
        c3 = Bar(900, 101.8, 103.0, 101.5, 102.8)
        gap = FairValueGap(Direction.BUY, 101.0, 101.5, c1.time, c2.time, c3.time)
        sweep = LiquiditySweep(Direction.BUY, 99.0, 98.5, 0, 300, "fixture-buy")
        candidate = V2Setup(
            c3_index=3,
            order_decision_index=4,
            direction=Direction.BUY,
            gap=gap,
            sweep=sweep,
            atr=1.0,
        )
        self.assertEqual(candidate.order_decision_index, 4)
        self.assertGreater(candidate.order_decision_index, candidate.c3_index)
        self.assertEqual(candidate.limit_price, 101.25)

    def test_buy_limit_fills_on_ask_low_not_bid_low(self):
        bars = [
            make_bar(datetime.datetime(2020, 1, 1, 10, 0), 100.70, 100.00, 100.50),
            make_bar(datetime.datetime(2020, 1, 1, 10, 5), 100.70, 100.00, 100.40),
        ]
        fill = pending_limit_fill(
            bars,
            Direction.BUY,
            100.20,
            submit_index=0,
            pending_expiry_bars=6,
            pending_expiry_seconds=1800,
        )
        self.assertIsNone(fill)  # Ask-low=100.30, so buy limit 100.20 is untouched.

    def test_sell_limit_fills_on_bid_high(self):
        bars = [
            make_bar(datetime.datetime(2020, 1, 1, 10, 0), 100.10, 99.80, 100.00),
            make_bar(datetime.datetime(2020, 1, 1, 10, 5), 100.21, 99.90, 100.10),
        ]
        fill = pending_limit_fill(
            bars,
            Direction.SELL,
            100.20,
            submit_index=0,
            pending_expiry_bars=6,
            pending_expiry_seconds=1800,
        )
        self.assertIsNotNone(fill)
        self.assertEqual(fill.entry_price, 100.20)
        self.assertEqual(fill.entry_index, 1)
        self.assertEqual(fill.decision_index, 0)

    def test_buy_limit_fills_when_ask_touches_the_limit(self):
        bars = [
            make_bar(datetime.datetime(2020, 1, 1, 10, 0), 100.70, 100.00, 100.50),
            make_bar(datetime.datetime(2020, 1, 1, 10, 5), 100.70, 99.90, 100.00),
        ]
        fill = pending_limit_fill(
            bars,
            Direction.BUY,
            100.20,
            submit_index=0,
            pending_expiry_bars=6,
            pending_expiry_seconds=1800,
        )
        self.assertIsNotNone(fill)
        self.assertEqual(fill.entry_price, 100.20)

    def test_pending_order_expires_at_six_bars_or_1800_seconds(self):
        bars = [
            make_bar(
                datetime.datetime(2020, 1, 1, 10, 0) + datetime.timedelta(minutes=5 * i),
                100.40,
                99.90,
                100.0,
            )
            for i in range(6)
        ]
        bars.append(
            make_bar(datetime.datetime(2020, 1, 1, 10, 30), 100.60, 99.90, 100.50)
        )
        fill = pending_limit_fill(
            bars,
            Direction.SELL,
            100.50,
            submit_index=0,
            pending_expiry_bars=6,
            pending_expiry_seconds=1800,
        )
        self.assertIsNone(fill)  # A touch at the exact expiry timestamp is too late.

    def test_pending_order_sees_no_bar_at_or_before_the_submission_bar(self):
        bars = [
            make_bar(datetime.datetime(2020, 1, 1, 10, 0), 100.90, 100.40, 100.80),
            make_bar(datetime.datetime(2020, 1, 1, 10, 5), 100.60, 100.00, 100.55),
        ]
        fill = pending_limit_fill(
            bars,
            Direction.SELL,
            100.50,
            submit_index=0,
            pending_expiry_bars=6,
            pending_expiry_seconds=1800,
        )
        self.assertIsNotNone(fill)
        self.assertEqual(fill.entry_index, 1)

    def test_expiry_uses_the_earlier_of_bar_and_second_limits(self):
        bars = [
            make_bar(
                datetime.datetime(2020, 1, 1, 10, 0) + datetime.timedelta(minutes=5 * i),
                100.40,
                99.90,
                100.0,
            )
            for i in range(6)
        ]
        bars.append(
            make_bar(datetime.datetime(2020, 1, 1, 10, 30), 100.60, 99.90, 100.50)
        )
        fill = pending_limit_fill(
            bars,
            Direction.SELL,
            100.50,
            submit_index=0,
            pending_expiry_bars=6,
            pending_expiry_seconds=60,
        )
        self.assertIsNone(fill)

    def test_fill_expiry_is_reported_on_a_successful_fill(self):
        bars = [
            make_bar(datetime.datetime(2020, 1, 1, 10, 0), 100.10, 99.80, 100.00),
            make_bar(datetime.datetime(2020, 1, 1, 10, 5), 100.21, 99.90, 100.10),
        ]
        fill = pending_limit_fill(
            bars,
            Direction.SELL,
            100.20,
            submit_index=0,
            pending_expiry_bars=6,
            pending_expiry_seconds=1800,
        )
        self.assertEqual(
            fill.expiry_time, bars[0].time + min(6 * 300, 1800)
        )

    def test_pending_limit_fill_rejects_invalid_arguments(self):
        bars = [make_bar(datetime.datetime(2020, 1, 1, 10, 0), 100.10, 99.80, 100.00)]
        with self.assertRaises(ValueError):
            pending_limit_fill(bars, Direction.BUY, 100.0, submit_index=5)
        with self.assertRaises(ValueError):
            pending_limit_fill(bars, Direction.BUY, 100.0, submit_index=0,
                               pending_expiry_bars=0)
        with self.assertRaises(ValueError):
            pending_limit_fill(bars, Direction.BUY, 100.0, submit_index=-1)


class ProtectionLevelTests(unittest.TestCase):
    def test_stop_floor_dominates_when_the_sweep_distance_is_small(self):
        buy_sl, buy_tp = protection_levels(Direction.BUY, 100.0, 98.0, 1.0, 0.01)
        sell_sl, sell_tp = protection_levels(Direction.SELL, 100.0, 102.0, 1.0, 0.01)
        self.assertEqual(buy_sl, 80.0)
        self.assertEqual(sell_sl, 120.0)
        self.assertEqual(buy_tp, 160.0)
        self.assertEqual(sell_tp, 40.0)

    def test_raw_stop_uses_half_atr_beyond_the_sweep_extreme(self):
        # A wide sweep distance is the only case where the raw formula is visible;
        # with the $20 floor dominating, a wrong sign would stay hidden.
        buy_sl, buy_tp = protection_levels(Direction.BUY, 100.0, 50.0, 1.0, 0.01)
        sell_sl, sell_tp = protection_levels(Direction.SELL, 100.0, 150.0, 1.0, 0.01)
        self.assertEqual(buy_sl, 49.5)
        self.assertEqual(sell_sl, 150.5)
        self.assertAlmostEqual(100.0 - buy_sl, sell_sl - 100.0)
        self.assertAlmostEqual(buy_tp - 100.0, 100.0 - sell_tp)

    def test_protection_is_symmetric_around_the_entry(self):
        buy_sl, buy_tp = protection_levels(Direction.BUY, 2000.0, 1995.0, 2.0, 0.01)
        sell_sl, sell_tp = protection_levels(Direction.SELL, 2000.0, 2005.0, 2.0, 0.01)
        self.assertEqual((buy_sl, buy_tp), (1980.0, 2060.0))
        self.assertEqual((sell_sl, sell_tp), (2020.0, 1940.0))
        self.assertAlmostEqual(buy_sl - 2000.0, 2000.0 - sell_sl)
        self.assertAlmostEqual(buy_tp - 2000.0, 2000.0 - sell_tp)

    def test_atr_distance_wins_when_it_exceeds_the_absolute_floor(self):
        sl, tp = protection_levels(Direction.BUY, 100.0, 90.0, 15.0, 0.01)
        self.assertEqual(sl, 55.0)  # 3 x ATR
        self.assertEqual(tp, 235.0)

    def test_widening_only_moves_the_stop_away_from_the_entry(self):
        tight = protection_levels(Direction.BUY, 2000.0, 1995.0, 2.0, 0.01)[0]
        loose = protection_levels(Direction.BUY, 2000.0, 1995.0, 30.0, 0.01)[0]
        self.assertLessEqual(loose, tight)

    def test_sell_stop_sits_above_the_sweep_high(self):
        sl, _ = protection_levels(Direction.SELL, 2000.0, 2005.0, 2.0, 0.01)
        self.assertGreater(sl, 2005.0)

    def test_protection_levels_round_to_tick_size(self):
        sl, tp = protection_levels(Direction.BUY, 100.0, 98.0, 1.0, 0.25)
        self.assertEqual(sl % 0.25, 0.0)
        self.assertEqual(tp % 0.25, 0.0)

    def test_protection_levels_validate_inputs(self):
        with self.assertRaises(ValueError):
            protection_levels(Direction.BUY, 100.0, 98.0, 0.0, 0.01)
        with self.assertRaises(ValueError):
            protection_levels(Direction.BUY, 100.0, 98.0, 1.0, 0.0)
        with self.assertRaises(ValueError):
            protection_levels(Direction.BUY, 0.0, 98.0, 1.0, 0.01)
        with self.assertRaises(ValueError):
            protection_levels(Direction.BUY, 100.0, 98.0, 1.0, 0.01, target_r=0.0)


class StructureWindowTests(unittest.TestCase):
    def test_h1_bos_confirmation_uses_the_bar_close_time(self):
        bos = StructureBreak(Direction.BUY, 100.0, 0, 0)
        shifted = bos_close_confirmation(bos, 3600)
        self.assertEqual(bos.confirmation_time, 0)
        self.assertEqual(shifted.confirmation_time, 3600)
        self.assertEqual(shifted.direction, Direction.BUY)
        self.assertEqual(shifted.level, 100.0)
        self.assertEqual(shifted.pivot_time, 0)
        with self.assertRaises(ValueError):
            bos_close_confirmation(bos, 0)

    def test_latest_bos_before_ignores_later_confirmations(self):
        first = StructureBreak(Direction.BUY, 100.0, 0, 1_000)
        second = StructureBreak(Direction.SELL, 101.0, 500, 2_000)
        events = [first, second]
        self.assertIs(latest_bos_before(events, 2_000), second)
        self.assertIs(latest_bos_before(events, 1_500), first)
        self.assertIsNone(latest_bos_before(events, 500))
        self.assertIsNone(latest_bos_before([], 2_000))

    def test_development_window_rejects_a_stale_bias(self):
        bos = StructureBreak(Direction.BUY, 100.0, 0, 0)
        sweep = LiquiditySweep(Direction.BUY, 99.0, 98.0, 300, 40_000, "stale")
        self.assertFalse(within_development_window(bos, sweep, V2Config()))

    def test_development_window_accepts_a_fresh_bias(self):
        bos = StructureBreak(Direction.BUY, 100.0, 0, 140_000)
        sweep = LiquiditySweep(Direction.BUY, 99.0, 98.0, 141_000, 143_600, "fresh")
        self.assertTrue(within_development_window(bos, sweep, V2Config()))
        self.assertFalse(
            within_development_window(
                StructureBreak(Direction.BUY, 100.0, 0, 200_000), sweep, V2Config()
            )
        )


class SweepEnumerationTests(unittest.TestCase):
    def test_sweep_is_reported_only_after_its_reclaim_bar_closed(self):
        bars = make_buy_setup_fixture()
        sweeps = enumerate_causal_sweeps(bars, V2Config(), 0.01, Direction.BUY)
        self.assertEqual(len(sweeps), 1)
        sweep = sweeps[0]
        self.assertEqual(sweep.level, 1990.0)
        self.assertEqual(sweep.extreme, 1989.0)
        self.assertEqual(sweep.confirmation_time, bars[494].time + 300)

    def test_appending_future_bars_does_not_change_earlier_sweeps(self):
        bars = make_buy_setup_fixture()
        prefix = bars[:495]
        before = enumerate_causal_sweeps(prefix, V2Config(), 0.01, Direction.BUY)
        after = enumerate_causal_sweeps(bars, V2Config(), 0.01, Direction.BUY)
        self.assertEqual(
            [(sweep.level, sweep.extreme, sweep.confirmation_time) for sweep in before],
            [(sweep.level, sweep.extreme, sweep.confirmation_time) for sweep in after[: len(before)]],
        )

    def test_ambiguous_same_bar_reclaims_are_rejected(self):
        # Two pool lows three ticks apart are swept and reclaimed by one bar, so the
        # level is structurally ambiguous and neither pool may produce a setup.
        bars = [
            make_indexed_bar(0, 102.0, 100.05, 100.5),
            make_indexed_bar(1, 102.0, 100.05, 100.5),
            make_indexed_bar(2, 102.0, 100.05, 100.5),
            make_indexed_bar(3, 102.0, 100.00, 100.5),   # pool A
            make_indexed_bar(4, 102.0, 100.05, 100.5),
            make_indexed_bar(5, 102.0, 100.05, 100.5),
            make_indexed_bar(6, 102.0, 100.05, 100.5),
            make_indexed_bar(7, 102.0, 100.03, 100.5),   # pool B, 3 ticks away
            make_indexed_bar(8, 102.0, 100.05, 100.5),
            make_indexed_bar(9, 102.0, 100.05, 100.5),
            make_indexed_bar(10, 102.0, 90.0, 95.0),    # sweeps both pools
            make_indexed_bar(11, 102.0, 95.0, 102.0),   # reclaims both pools
        ]
        sweeps = enumerate_causal_sweeps(bars, V2Config(), 0.01, Direction.BUY)
        self.assertEqual(sweeps, [])

    def test_near_equal_pools_in_one_bar_share_a_single_sweep(self):
        # One tick apart, the two pools are the same liquidity level, so the latest
        # confirmed pivot anchors it and the setup stays valid.
        bars = [
            make_indexed_bar(0, 102.0, 100.05, 100.5),
            make_indexed_bar(1, 102.0, 100.05, 100.5),
            make_indexed_bar(2, 102.0, 100.05, 100.5),
            make_indexed_bar(3, 102.0, 100.00, 100.5),
            make_indexed_bar(4, 102.0, 100.05, 100.5),
            make_indexed_bar(5, 102.0, 100.05, 100.5),
            make_indexed_bar(6, 102.0, 100.05, 100.5),
            make_indexed_bar(7, 102.0, 100.01, 100.5),
            make_indexed_bar(8, 102.0, 100.05, 100.5),
            make_indexed_bar(9, 102.0, 100.05, 100.5),
            make_indexed_bar(10, 102.0, 90.0, 95.0),
            make_indexed_bar(11, 102.0, 95.0, 102.0),
        ]
        sweeps = enumerate_causal_sweeps(bars, V2Config(), 0.01, Direction.BUY)
        self.assertEqual(len(sweeps), 1)

    def test_a_sweep_without_a_reclaim_in_the_window_is_not_reported(self):
        bars = make_buy_setup_fixture()
        truncated = list(bars[:495])
        truncated[494] = make_indexed_bar(494, 2005.0, 1989.0, 1989.5, 1989.5)
        sweeps = enumerate_causal_sweeps(truncated, V2Config(), 0.01, Direction.BUY)
        self.assertEqual(sweeps, [])


class DisplacementCausalityTests(unittest.TestCase):
    def test_displacement_must_break_an_internal_pivot_formed_after_the_sweep(self):
        bars = make_buy_setup_fixture()
        sweeps = enumerate_causal_sweeps(bars, V2Config(), 0.01, Direction.BUY)
        self.assertIsNotNone(find_post_sweep_displacement(bars, sweeps[0], V2Config()))

    def test_a_pure_displacement_without_an_internal_break_is_rejected(self):
        bars = make_buy_setup_fixture()
        # Flatten the internal pivot high: bar 496 no longer stands above its
        # neighbours, so nothing was broken and there is no CHOCH to trade.
        bars[496] = make_indexed_bar(496, 2005.0, 1996.0, 2005.0, 2004.0)
        sweeps = enumerate_causal_sweeps(bars, V2Config(), 0.01, Direction.BUY)
        self.assertEqual(len(sweeps), 1)
        self.assertIsNone(find_post_sweep_displacement(bars, sweeps[0], V2Config()))
        report = build_v2_candidates(bars, make_buy_bias_h1_fixture(), V2Config())
        self.assertEqual(report.candidates, [])
        self.assertEqual(report.rejections["no_post_sweep_displacement_choch"], 1)


class CandidateBuilderTests(unittest.TestCase):
    def test_buy_candidate_orders_on_the_first_bar_after_c3(self):
        m5 = make_buy_setup_fixture()
        h1 = make_buy_bias_h1_fixture()
        report = build_v2_candidates(m5, h1, V2Config())
        self.assertEqual(len(report.candidates), 1)
        candidate = report.candidates[0]
        self.assertEqual(candidate.direction, Direction.BUY)
        self.assertEqual(candidate.c3_index, 500)
        self.assertEqual(candidate.order_decision_index, 501)
        self.assertEqual(candidate.gap.lower, 2012.0)
        self.assertEqual(candidate.gap.upper, 2014.0)
        self.assertEqual(candidate.limit_price, 2013.0)
        self.assertEqual(report.rejections["v2_wrong_previous_d1_premium_discount"], 0)
        self.assertEqual(report.rejections["v2_outside_rolling_high_conviction_session"], 0)

    def test_candidate_is_invariant_to_appending_future_bars(self):
        m5 = make_buy_setup_fixture()
        h1 = make_buy_bias_h1_fixture()
        before = build_v2_candidates(m5, h1, V2Config())
        extended = m5 + [make_indexed_bar(len(m5), 2500.0, 2400.0, 2450.0)]
        after = build_v2_candidates(extended, h1, V2Config())
        self.assertEqual(
            [
                (c.c3_index, c.order_decision_index, c.limit_price)
                for c in before.candidates
            ],
            [
                (c.c3_index, c.order_decision_index, c.limit_price)
                for c in after.candidates[: len(before.candidates)]
            ],
        )

    def test_missing_bias_is_recorded_as_a_rejection(self):
        m5 = make_buy_setup_fixture()
        report = build_v2_candidates(m5, [], V2Config())
        self.assertEqual(report.candidates, [])
        self.assertEqual(report.rejections["no_matching_confirmed_h1_bos"], 1)

    def test_report_counts_sweep_rejection_reasons(self):
        m5 = make_buy_setup_fixture()
        h1 = make_buy_bias_h1_fixture()
        report = build_v2_candidates(m5, h1, V2Config())
        self.assertEqual(sum(report.rejections.values()), 0)
        self.assertIn("EXPLORATORY_BAR_MODEL", report.model_kind)


class RuntimeObserveOnlyTests(unittest.TestCase):
    """Static guards: the shipped MQL V2 must be identifiable and fail closed."""

    def root(self):
        return Path(__file__).resolve().parents[1]

    def test_v2_observe_only_gates_order_submission_before_submit_call(self):
        source = (self.root() / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        guard = source.index("if(g_cfg_observe_only)")
        submit = source.index("SubmitMidpointLimit(", guard)
        block = source[guard:submit]
        self.assertIn('"observe_only_candidate"', block)
        self.assertIn("return;", block)

    def test_observe_only_gate_precedes_every_order_submission_call(self):
        source = (self.root() / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        guard = source.index("if(g_cfg_observe_only)")
        self.assertEqual(source.count("SubmitMidpointLimit("), 1)
        self.assertLess(guard, source.index("SubmitMidpointLimit("))
        # The EA must not be able to bypass its own gate.
        for call in ("OrderSend(", "OrderCheck("):
            self.assertNotIn(call, source, f"{call} must stay inside SMC_Execution")

    def test_v2_has_distinct_variant_and_event_log_from_b3(self):
        logger = (self.root() / "Include/SMC_Logger.mqh").read_text(encoding="utf-8")
        config = (self.root() / "Include/SMC_Config.mqh").read_text(encoding="utf-8")
        # Identity lives in one place; the logger must consume it, never re-hardcode it.
        self.assertIn('"V2_PARITY"', config)
        self.assertIn('"SMC_H1M5_SweepFVG_V2_events.csv"', config)
        self.assertIn("SMC_V2_EVENT_LOG", logger)
        self.assertIn("SMC_V2_VARIANT", logger)
        self.assertNotIn('SMC_VERSION,"B3"', logger)
        self.assertNotIn('"SMC_H1M5_SweepFVG_events.csv"', logger)

    def test_v2_identity_constants_do_not_collide_with_b3_magic(self):
        config = (self.root() / "Include/SMC_Config.mqh").read_text(encoding="utf-8")
        self.assertIn("#define SMC_V2_MAGIC", config)
        self.assertIn("15101602", config)
        self.assertIn("#define SMC_V2_VERSION", config)
        source = (self.root() / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        self.assertNotIn("InpSMCMagic                          = 15101501", source)

    def test_cost_unverified_is_allowed_only_in_observe_only(self):
        config = (self.root() / "Include/SMC_Config.mqh").read_text(encoding="utf-8")
        runtime = (self.root() / "Include/SMC_RuntimeConfig.mqh").read_text(encoding="utf-8")
        self.assertIn("!cost_model_verified && !observe_only", config)
        self.assertIn("OBSERVE_ONLY", runtime)
        self.assertFalse(may_submit_order(cost_verified=False, observe_only=True))
        self.assertFalse(may_submit_order(cost_verified=False, observe_only=False))
        self.assertFalse(may_submit_order(cost_verified=True, observe_only=True))
        self.assertTrue(may_submit_order(cost_verified=True, observe_only=False))

    def test_risk_manager_stays_fail_closed_without_verified_costs(self):
        source = (self.root() / "Include/SMC_RiskManager.mqh").read_text(encoding="utf-8")
        self.assertIn("!cost_model_verified && !observe_only", source)
        for method in ("CanOpenTrade", "CalculateVolume", "CalculateNetRewardRisk"):
            self.assertIn(f"bool {method}", source)
        self.assertGreaterEqual(
            source.count("!m_cost_model_verified"), 3, "every money path must be gated"
        )


class ProtectionParityTests(unittest.TestCase):
    """Static guards: the shipped MQL must reproduce the Python golden vectors."""

    def root(self):
        return Path(__file__).resolve().parents[1]

    def test_mql_computes_the_same_protection_golden_vector_as_python(self):
        filters = (self.root() / "Include/SMC_Filters.mqh").read_text(encoding="utf-8")
        self.assertIn("SMC_ProtectionLevels", filters)
        self.assertIn("SMC_V2ProtectionSelfTest", filters)
        for price in ("1980.0", "2060.0", "2020.0", "1940.0"):
            self.assertIn(price, filters)
        # The raw stop must sit half an ATR *beyond* the sweep on the correct side:
        # minus for BUY, plus for SELL. A wrong sign survives the $20 floor whenever
        # the sweep distance is the wider of the two.
        self.assertIn(
            "is_buy ? sweep_extreme-0.5*atr_value : sweep_extreme+0.5*atr_value", filters
        )
        buy_sl, buy_tp = protection_levels(Direction.BUY, 2000.0, 1995.0, 2.0, 0.01)
        sell_sl, sell_tp = protection_levels(Direction.SELL, 2000.0, 2005.0, 2.0, 0.01)
        self.assertEqual((buy_sl, buy_tp), (1980.0, 2060.0))
        self.assertEqual((sell_sl, sell_tp), (2020.0, 1940.0))

    def test_execution_never_replaces_the_final_v2_levels(self):
        execution = (self.root() / "Include/SMC_Execution.mqh").read_text(encoding="utf-8")
        self.assertNotIn("setup.sweep_extreme-0.5*setup.atr_value", execution)
        self.assertNotIn("setup.sweep_extreme+0.5*setup.atr_value", execution)
        self.assertIn("request.sl=setup.stop_loss", execution)
        self.assertIn("request.tp=setup.take_profit", execution)

    def test_ea_runs_the_protection_self_test_at_init(self):
        source = (self.root() / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        self.assertIn("SMC_V2ProtectionSelfTest()", source)
        self.assertIn("v2_protection_self_test_failed", source)

    def test_session_filter_reads_closed_bars_only(self):
        filters = (self.root() / "Include/SMC_Filters.mqh").read_text(encoding="utf-8")
        self.assertIn("CopyRates(symbol,PERIOD_M5,1,needed,rates)", filters)
        self.assertNotIn("CopyRates(symbol,PERIOD_M5,0,needed,rates)", filters)

    def test_session_result_is_cached_per_new_decision_bar(self):
        filters = (self.root() / "Include/SMC_Filters.mqh").read_text(encoding="utf-8")
        self.assertIn("g_smc_session_cache_bar", filters)
        self.assertIn("g_smc_session_cache_value", filters)
        # The cache must actually short-circuit, and must be keyed on the decision
        # bar time; otherwise it either recomputes every call or never refreshes.
        self.assertIn(
            "if(g_smc_session_cache_valid && g_smc_session_cache_bar==decision_bar_time)",
            filters,
        )
        self.assertIn("return g_smc_session_cache_value;", filters)
        self.assertIn("g_smc_session_cache_bar=decision_bar_time;", filters)
        source = (self.root() / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        self.assertIn("SMC_SessionAllowedCached(", source)
        self.assertIn("iTime(g_cfg_symbol,SMC_EXECUTION_TF,0)", source)
        self.assertNotIn("if(!SMC_SessionAllowed(g_cfg_symbol))", source)

    def test_v2_does_not_retarget_a_pending_order_to_next_liquidity(self):
        source = (self.root() / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        self.assertNotIn("ModifyPendingTarget(", source)
        self.assertNotIn("next_liquidity_target_disappeared", source)

    def test_every_closed_m5_bar_emits_a_processed_record(self):
        source = (self.root() / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        self.assertIn('"m5_bar_processed"', source)
        self.assertIn("bar=%s;state=%s", source)
        handler = source[source.index("void HandleNewClosedM5()") :]
        handler = handler[: handler.index("void ManagePendingSetup")]
        self.assertIn('"m5_bar_processed"', handler)

    def test_runtime_logs_use_the_effective_config_not_raw_inputs(self):
        source = (self.root() / "Experts/SMC_H1M5_SweepFVG.mq5").read_text(encoding="utf-8")
        allowed_stripped = (
            "input double         InpSMCRiskPercent",
            "input long           InpSMCMagic",
            "double cfg_risk=InpSMCRiskPercent;",
            "long   cfg_magic=InpSMCMagic;",
        )
        offenders = [
            line
            for line in source.split("\n")
            if ("InpSMCRiskPercent" in line or "InpSMCMagic" in line)
            and not line.strip().startswith(allowed_stripped)
        ]
        self.assertEqual(offenders, [], "runtime paths must log g_cfg_* values")


class CostModelTests(unittest.TestCase):
    def test_unverified_costs_are_marked_as_assumptions(self):
        costs = CostModel()
        self.assertFalse(costs.verified)
        self.assertEqual(costs.slippage_price, 0.10)
        self.assertEqual(costs.commission_price, 0.0)
        self.assertIn("UNVERIFIED", costs.status())

    def test_cost_model_validates_its_inputs(self):
        with self.assertRaises(ValueError):
            CostModel(contract_size=0.0)
        with self.assertRaises(ValueError):
            CostModel(slippage_per_lot=-1.0)
        with self.assertRaises(ValueError):
            CostModel(commission_per_lot=-1.0)

    def test_net_r_accounts_for_spread_slippage_and_commission(self):
        trade = Trade(
            setup_id="x", direction=Direction.BUY, entry_index=1, entry_time=0,
            entry_price=100.0, stop_loss=80.0, take_profit=160.0, distance=20.0,
            exit_index=2, exit_time=300, exit_price=160.0, exit_reason="take_profit",
            gross_r=3.0, spread_price=0.30, slippage_price=0.10, commission_price=0.02,
        )
        costs = CostModel(commission_per_lot=2.0, slippage_per_lot=10.0)
        # The spread is already inside the fill/exit prices (a BUY limit only fills
        # when the Ask trades down to it), so only slippage and commission are added.
        self.assertAlmostEqual(trade.net_r(costs), 3.0 - (0.10 + 0.02) / 20.0)
        self.assertAlmostEqual(trade.net_r(costs), trade.gross_r - 0.006)


class PendingSimulationTests(unittest.TestCase):
    def build_candidate(self):
        m5 = make_buy_setup_fixture()
        report = build_v2_candidates(m5, make_buy_bias_h1_fixture(), V2Config())
        self.assertEqual(len(report.candidates), 1)
        return m5, report.candidates[0]

    def test_limit_fill_is_simulated_after_the_decision_bar(self):
        m5, candidate = self.build_candidate()
        bars = m5 + [
            make_indexed_bar(502, 2035.0, 2012.0, 2030.0),
            make_indexed_bar(503, 2140.0, 2025.0, 2135.0),
        ]
        trades = simulate_v2_pending([candidate], bars, V2Config(), CostModel())
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade.direction, Direction.BUY)
        self.assertEqual(trade.entry_price, 2013.0)
        self.assertEqual(trade.entry_index, 502)
        self.assertEqual(trade.exit_reason, "take_profit")
        # 3R up to tick rounding: the target is snapped to the 0.01 grid, so the
        # realised R differs from exactly 3 by a few ten-thousandths.
        self.assertAlmostEqual(trade.gross_r, 3.0, places=2)

    def test_stop_exit_is_minus_one_r(self):
        m5, candidate = self.build_candidate()
        bars = m5 + [
            make_indexed_bar(502, 2035.0, 2012.0, 2030.0),
            make_indexed_bar(503, 2030.0, 1960.0, 1970.0),
        ]
        trades = simulate_v2_pending([candidate], bars, V2Config(), CostModel())
        self.assertEqual(trades[0].exit_reason, "stop_loss")
        self.assertAlmostEqual(trades[0].gross_r, -1.0, places=2)

    def test_same_bar_sl_and_tp_resolve_to_stop_first(self):
        m5, candidate = self.build_candidate()
        bars = m5 + [
            make_indexed_bar(502, 2035.0, 2012.0, 2030.0),
            make_indexed_bar(503, 2200.0, 1900.0, 2100.0),
        ]
        trades = simulate_v2_pending([candidate], bars, V2Config(), CostModel())
        self.assertEqual(trades[0].exit_reason, "stop_loss")

    def test_unfilled_pending_orders_produce_no_trade(self):
        m5, candidate = self.build_candidate()
        bars = m5 + [make_indexed_bar(502, 2035.0, 2025.0, 2030.0) for _ in range(4)]
        trades = simulate_v2_pending([candidate], bars, V2Config(), CostModel())
        self.assertEqual(trades, [])

    def test_protection_is_symmetric_for_a_sell_setup(self):
        m5 = make_sell_setup_fixture()
        report = build_v2_candidates(m5, make_sell_bias_h1_fixture(), V2Config())
        self.assertEqual(len(report.candidates), 1)
        candidate = report.candidates[0]
        self.assertEqual(candidate.direction, Direction.SELL)
        bars = m5 + [
            make_indexed_bar(502, 2014.0, 1990.0, 2000.0),   # Bid high fills the sell limit
            make_indexed_bar(503, 2010.0, 1860.0, 1870.0),   # Ask low reaches the target
        ]
        trades = simulate_v2_pending([candidate], bars, V2Config(), CostModel())
        self.assertEqual(len(trades), 1)
        stop, target = protection_levels(
            Direction.SELL, candidate.limit_price, candidate.sweep.extreme, candidate.atr, 0.01
        )
        self.assertGreater(stop, candidate.limit_price)
        self.assertLess(target, candidate.limit_price)
        self.assertEqual(trades[0].exit_reason, "take_profit")
        self.assertAlmostEqual(trades[0].gross_r, 3.0, places=2)

    def test_metrics_report_r_expectancy_and_drawdown(self):
        m5, candidate = self.build_candidate()
        bars = m5 + [
            make_indexed_bar(502, 2035.0, 2012.0, 2030.0),
            make_indexed_bar(503, 2140.0, 2025.0, 2135.0),
        ]
        trades = simulate_v2_pending([candidate], bars, V2Config(), CostModel())
        metrics = summarise_trades(trades, CostModel())
        self.assertEqual(metrics["trades"], 1)
        self.assertAlmostEqual(metrics["expectancy_r"], trades[0].net_r(CostModel()), places=6)
        self.assertEqual(metrics["max_drawdown_r"], 0.0)
        self.assertAlmostEqual(metrics["profit_factor"], float("inf"))
        self.assertEqual(metrics["win_rate"], 1.0)
        self.assertEqual(summarise_trades([], CostModel())["trades"], 0)


class ExploratoryRunTests(unittest.TestCase):
    def write_fixture(self):
        directory = Path(tempfile.mkdtemp())
        paths = {}
        for name, bars, period in (
            ("m5", make_buy_setup_fixture(), 5),
            ("h1", make_buy_bias_h1_fixture(), 60),
        ):
            path = directory / f"{name}.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    ["datetime", "open", "high", "low", "close", "tick_volume", "spread_points"]
                )
                for index, bar in enumerate(bars):
                    stamp = stamp_for(index, period)
                    writer.writerow([
                        stamp.strftime("%Y.%m.%d %H:%M:%S"), bar.open, bar.high, bar.low,
                        bar.close, 10, int(round(bar.spread_price / 0.01)),
                    ])
            paths[name] = path
        manifest = directory / "manifest.txt"
        manifest.write_text("run_id=fixture\nstatus=complete\n", encoding="utf-8")
        return paths["m5"], paths["h1"], manifest

    def test_report_is_exploratory_and_forbids_oos_claims(self):
        m5_path, h1_path, manifest = self.write_fixture()
        out = Path(tempfile.mkdtemp()) / "nested" / "v2_report.json"
        result = run_v2_exploratory(
            m5_path, h1_path, manifest, V2Config(),
            datetime.datetime(2020, 1, 1), datetime.datetime(2020, 1, 3),
            out,
        )
        self.assertTrue(out.exists())
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["model_kind"], "EXPLORATORY_BAR_MODEL")
        self.assertFalse(payload["oos_claim_permitted"])
        self.assertEqual(payload["acceptance_status"], "INCONCLUSIVE")
        self.assertEqual(payload["cost_assumptions"]["commission_status"], "UNVERIFIED")
        self.assertIn("m5_sha256", payload["raw_inputs"])
        self.assertIn("h1_sha256", payload["raw_inputs"])
        self.assertEqual(payload["window"]["dev_end"], "2020-01-03 00:00:00")
        self.assertTrue(payload["window"]["burned_oos_excluded"])
        self.assertEqual(payload["config"]["observe_only"], True)

    def test_runner_never_reads_rows_after_the_dev_window(self):
        m5_path, h1_path, manifest = self.write_fixture()
        out = Path(tempfile.mkdtemp()) / "report.json"
        payload = run_v2_exploratory(
            m5_path, h1_path, manifest, V2Config(),
            datetime.datetime(2020, 1, 1), datetime.datetime(2020, 1, 2, 18, 30),
            out,
        )
        self.assertEqual(payload["data"]["last_bar"], "2020-01-02 17:45:00")
        self.assertLessEqual(payload["window"]["dev_end"], "2020-01-02 18:30:00")

    def test_runner_rejects_an_end_before_the_start(self):
        m5_path, h1_path, manifest = self.write_fixture()
        with self.assertRaises(ValueError):
            run_v2_exploratory(
                m5_path, h1_path, manifest, V2Config(),
                datetime.datetime(2020, 1, 2), datetime.datetime(2020, 1, 1),
                Path(tempfile.mkdtemp()) / "report.json",
            )

    def test_cli_writes_the_same_report(self):
        m5_path, h1_path, manifest = self.write_fixture()
        out = Path(tempfile.mkdtemp()) / "cli_report.json"
        main([
            "--m5-path", str(m5_path), "--h1-path", str(h1_path),
            "--export-manifest", str(manifest),
            "--dev-start", "2020-01-01 00:00:00", "--dev-end", "2020-01-03 00:00:00",
            "--output", str(out),
        ])
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["model_kind"], "EXPLORATORY_BAR_MODEL")


if __name__ == "__main__":
    unittest.main()