import csv
import math
import unittest
from datetime import datetime, timedelta

import study as s


class TimingTests(unittest.TestCase):
    def test_exact_lookback_completed_candles_and_settled_rate_exclusion(self):
        h = s.START
        rates = {
            h - s.LOOKBACK: dict(time=h - s.LOOKBACK + timedelta(milliseconds=10), funding_rate=-9.),
            h - s.LOOKBACK + s.HOUR: dict(time=h-s.LOOKBACK+s.HOUR, funding_rate=-2.),
            h - s.HOUR: dict(time=h-s.HOUR, funding_rate=0.),
            h: dict(time=h + timedelta(milliseconds=20), funding_rate=-5.),
        }
        candles = {h - 25*s.HOUR: {"close": 100}, h-s.HOUR: {"close": 103},
                   h: {"close": 90}}
        event, = s.signals(rates, candles)
        self.assertEqual(event["lookback_count"], 2)
        self.assertAlmostEqual(event["first_percentile"], -1.98)
        self.assertAlmostEqual(event["trailing_return"], .03)
        self.assertTrue(event["proposed"])
        self.assertEqual(s.window(event, 1), (h+s.HOUR, h+25*s.HOUR))

    def test_cooldown_and_exclusive_end(self):
        h = s.START
        events = [dict(signal_hour=t, proposed=True) for t in
                  (h, h + 48*s.HOUR, h + 49*s.HOUR, s.END - 25*s.HOUR)]
        candles = {t: {} for t in s.hours(s.START, s.END)}
        self.assertEqual(s.choose(events, "primary", candles), [events[0], events[2]])
        self.assertEqual(events[1]["primary"], "position_or_cooldown")
        self.assertEqual(events[3]["primary"], "development_boundary")

    def test_held_time_boundaries_and_adverse_sign(self):
        start = s.START
        h = start + s.HOUR
        exit_ = h + s.DAY
        event = dict(signal_hour=start, settled_at=start, funding_rate=-.001,
                     first_percentile=-.0005, trailing_return=.01)
        candles = {t: dict(open=100., volume=100.) for t in s.hours(h, exit_+s.HOUR)}
        rates = {t: dict(time=t, funding_rate=0.) for t in candles}
        rates[h] = dict(time=h+timedelta(milliseconds=1), funding_rate=-.0003)
        rates[h+s.HOUR] = dict(time=h+s.HOUR, funding_rate=.0002)
        rates[exit_] = dict(time=exit_, funding_rate=.999)
        oracle = {t: dict(oracle_price=100.) for t in s.hours(start, exit_)}
        row, flows = s.build_trade(event, "primary", 1, None, candles, rates,
                                   oracle, sorted(oracle), set())
        self.assertEqual(len(flows), 24)
        self.assertAlmostEqual(flows[0]["adverse_10"], .00027)
        self.assertAlmostEqual(flows[1]["adverse_10"], -.00022)
        self.assertAlmostEqual(row["funding_oracle"], .0001)
        self.assertAlmostEqual(row["funding_exclude_entry_hour"], -.0002)
        self.assertAlmostEqual(row["base_zero_net"], -.002)
        self.assertAlmostEqual(row["stress_zero_net"], -.0044)
        rates[h]["time"] = h
        _, flows = s.build_trade(event, "primary", 1, None, candles, rates,
                                 oracle, sorted(oracle), set())
        self.assertEqual(len(flows), 23)


class LedgerTests(unittest.TestCase):
    def test_primary_signal_and_cost_ledger(self):
        funding, candles, oracle, _ = s.validate()
        with (s.OUT / "events.csv").open() as handle:
            events = list(csv.DictReader(handle))
        # Independent scan of original settled timestamps and OHLC close rows.
        samples = list(funding.values())
        for event in events:
            h = datetime.fromisoformat(event["signal_hour"])
            t = datetime.fromisoformat(event["settled_at"])
            lookback = sorted(r["funding_rate"] for r in samples
                              if t-timedelta(days=90) <= r["time"] < t)
            index = (len(lookback)-1)/100
            fraction = index % 1
            p1 = (1-fraction)*lookback[math.floor(index)] + fraction*lookback[math.ceil(index)]
            prior = candles[h-timedelta(hours=1)]["close"] / candles[h-timedelta(hours=25)]["close"]-1
            self.assertAlmostEqual(float(event["first_percentile"]), p1)
            self.assertEqual(event["proposed"], str(prior > 0 and
                                                    float(event["funding_rate"]) <= p1 and
                                                    float(event["funding_rate"]) < 0))

        with (s.OUT / "trades.csv").open() as handle:
            trades = list(csv.DictReader(handle))
        last_exit = {}
        for trade in trades:
            kind = trade["strategy"]
            h, entry, exit_ = [datetime.fromisoformat(trade[k])
                               for k in ("signal_hour", "entry", "exit")]
            self.assertEqual(entry-h, timedelta(hours=2 if kind == "delay_2h" else 1))
            self.assertEqual(exit_-entry, timedelta(days=1))
            self.assertLess(exit_, s.END)
            if kind in last_exit:
                self.assertGreaterEqual(h-last_exit[kind], timedelta(days=1))
            last_exit[kind] = exit_
            p0, p1 = candles[entry]["open"], candles[exit_]["open"]
            actual = [(r["funding_rate"], bucket) for bucket, r in funding.items()
                      if entry < r["time"] < exit_]
            flows = [-rate * oracle[bucket-s.HOUR]["oracle_price"] / p0 for rate, bucket in actual]
            net_funding = sum(flows)
            self.assertAlmostEqual(float(trade["funding_oracle"]), net_funding)
            self.assertAlmostEqual(float(trade["funding_adverse_10"]),
                                   net_funding - .10*sum(map(abs, flows)))
            self.assertAlmostEqual(float(trade["base_oracle_net"]),
                                   (p1-p0-.001*(p0+p1))/p0+net_funding)
            self.assertAlmostEqual(float(trade["stress_oracle_net"]),
                                   (p1-p0-.00195*(p0+p1))/p0+net_funding-.0005)
            for mode in ("base", "stress"):
                pnl = float(trade[f"{mode}_net_eur_5000"])
                self.assertAlmostEqual(float(trade[f"{mode}_net_eur_10000"]), 2*pnl)


if __name__ == "__main__":
    unittest.main()
