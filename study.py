"""Discovery 014 development replay. This module has no network access."""
import bisect
import csv
import hashlib
import json
import math
import random
import statistics as stats
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "results"
WARMUP = datetime(2025, 5, 25, tzinfo=UTC)
START = datetime(2025, 9, 1, tzinfo=UTC)
END = datetime(2026, 7, 1, tzinfo=UTC)
MID = datetime(2026, 2, 1, tzinfo=UTC)
CRASH = datetime(2025, 10, 10, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = 24 * HOUR
LOOKBACK = 90 * DAY
ALLOCATION = .40
COST = {"base": (.00045, .00015, .00040, 0),
        "stress": (.00045, .00030, .00120, .00050)}
SCENARIOS = {"oracle": 0, "adverse_1": .01, "adverse_3": .03,
             "adverse_10": .10, "favorable_10": -.10}
STRATEGIES = ("primary", "all_extreme", "negative_return", "matched_positive",
              "matched_passive", "delay_2h")
BLOCK_COUNT = math.ceil((END - START).days / 7)


def hours(first, end):
    return [first + i * HOUR for i in range((end - first) // HOUR)]


def slot(timestamp):
    return timestamp.replace(minute=0, second=0, microsecond=0)


def percentile(values, probability):
    ordered = sorted(values)
    index = probability * (len(ordered) - 1)
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (index - low) * (ordered[high] - ordered[low])


def valid_price(value):
    return value is not None and value > 0 and math.isfinite(value)


def load_series(kind, allowed_start):
    with (DATA / f"{kind}.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = {}
    for row in rows:
        stamp = datetime.fromisoformat(row.pop("timestamp"))
        if not allowed_start <= stamp < END or stamp.utcoffset() != timedelta(0):
            raise ValueError(f"{kind}: out of scope")
        hour = slot(stamp)
        if hour in result or (kind != "funding" and stamp != hour):
            raise ValueError(f"{kind}: invalid time grid at {stamp}")
        result[hour] = {key: float(value) if value else None for key, value in row.items()}
        result[hour]["time"] = stamp
    return result


def reconcile(source, funding, candles):
    res = {}
    for name, records in source.items():
        fields = ({"fundingRate": "funding_rate", "premium": "premium"} if name == "funding"
                  else dict(o="open", h="high", l="low", c="close", v="volume", n="trades"))
        data = funding if name == "funding" else candles
        mismatches = {field: [] for field in fields.values()}
        if len(records["rows"]) != 24:
            raise ValueError("Official sample size differs")
        for row in records["rows"]:
            stamp = datetime.fromtimestamp(row["time" if name == "funding" else "t"] / 1000, UTC)
            if stamp.date().isoformat() != "2026-06-24":
                raise ValueError("Official sample date differs")
            item = data[slot(stamp)]
            if item["time"] != stamp or (name == "candle" and row["T"] - row["t"] != 3599999):
                raise ValueError("Timestamp mismatch in official reconciliation")
            for key, field in fields.items():
                if not math.isclose(float(row[key]), item[field], rel_tol=1e-12, abs_tol=1e-12):
                    mismatches[field].append(
                        dict(time=stamp, archive=item[field], official=float(row[key])))
        essential = ("funding_rate", "premium") if name == "funding" else ("open", "close")
        if any(mismatches[key] for key in essential):
            raise ValueError("Essential official series mismatch")
        res[name] = dict(matched_timestamps=24, mismatches=mismatches)
    return res


def validate():
    manifest = json.loads((DATA / "manifest.json").read_text())
    paths = {"funding.csv", "candle.csv", "oracle.csv", "official-june24.json"}
    if set(manifest["retained_sha256"]) != paths:
        raise ValueError("Input manifest has unexpected paths")
    for name, expected in manifest["retained_sha256"].items():
        if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Retained input hash mismatch: {name}")

    funding = load_series("funding", WARMUP)
    candles = load_series("candle", WARMUP)
    oracle = load_series("oracle", START)
    report = {}
    for name, data, first in (("funding", funding, WARMUP), ("candle", candles, WARMUP),
                              ("oracle", oracle, START)):
        missing = sorted(set(hours(first, END)) - data.keys())
        if name == "funding" and missing:
            raise ValueError("Incomplete funding grid")
        if name == "candle" and any(h >= START - 25 * HOUR for h in missing):
            raise ValueError("Missing development signal or mark candle")
        if name == "oracle" and missing:
            raise ValueError("Oracle hour grid has absent rows")
        report[name] = dict(rows=len(data), first=data[min(data)]["time"],
                            last=data[max(data)]["time"], duplicate_hours=0, missing_hours=missing)
    for h, row in funding.items():
        if row["funding_rate"] is None or not math.isfinite(row["funding_rate"]):
            raise ValueError(f"Invalid funding rate at {h}")
    for h, row in candles.items():
        prices = [row[key] for key in ("open", "high", "low", "close")]
        if (not all(valid_price(price) for price in prices)
                or not row["low"] <= min(row["open"], row["close"])
                <= max(row["open"], row["close"]) <= row["high"]
                or row["trades"] <= 0 or row["volume"] <= 0
                or not math.isfinite(row["volume"])):
            raise ValueError(f"Invalid traded candle at {h}")
    if any(v["oracle_price"] is not None and not valid_price(v["oracle_price"])
           for v in oracle.values()):
        raise ValueError("Invalid oracle price")
    report["oracle"]["null_price_hours"] = [h for h, row in oracle.items()
                                           if row["oracle_price"] is None]
    report["funding"]["maximum_delay_seconds"] = max(
        (row["time"] - h).total_seconds() for h, row in funding.items())
    report["funding"]["maximum_development_delay_seconds"] = max(
        (row["time"] - h).total_seconds() for h, row in funding.items() if h >= START)
    report["official_sample"] = reconcile(
        json.loads((DATA / "official-june24.json").read_text()), funding, candles)
    return funding, candles, oracle, report


def signals(funding, candles):
    timeline = sorted(funding)
    timestamps = [funding[h]["time"] for h in timeline]
    rates = [funding[h]["funding_rate"] for h in timeline]
    events = []
    for i, (h, timestamp, rate) in enumerate(zip(timeline, timestamps, rates)):
        if h < START:
            continue
        left = bisect.bisect_left(timestamps, timestamp - LOOKBACK)
        threshold = percentile(rates[left:i], .01)
        prior = candles[h - HOUR]["close"] / candles[h - 25 * HOUR]["close"] - 1
        events.append(dict(signal_hour=h, settled_at=timestamp, funding_rate=rate,
                           lookback_count=i-left, first_percentile=threshold,
                           trailing_return=prior, extreme=rate < 0 and rate <= threshold,
                           proposed=rate < 0 and rate <= threshold and prior > 0))
    return events


def window(event, delay):
    entry = event["signal_hour"] + delay * HOUR
    return entry, entry + DAY


def choose(events, kind, candles, delay=1):
    accepts = {"primary": lambda e: e["proposed"], "all_extreme": lambda e: e["extreme"],
               "negative_return": lambda e: e["extreme"] and e["trailing_return"] < 0}
    chosen = []
    ready = START
    ledger_name = kind if delay == 1 else "delay_2h"
    for event in events:
        entry, exit_ = window(event, delay)
        if not accepts[kind](event):
            disposition = "signal_filter"
        elif event["signal_hour"] < ready:
            disposition = "position_or_cooldown"
        elif exit_ >= END:
            disposition = "development_boundary"
        elif any(h not in candles for h in hours(entry, exit_ + HOUR)):
            disposition = "missing_candle"
        else:
            disposition = "entered"
            chosen.append(event)
            ready = exit_ + DAY
        event[ledger_name] = disposition
    return chosen


def match(events, primary, kind, candles):
    eligible = [e for e in events if window(e, 1)[1] < END
                and all(h in candles for h in hours(*window(e, 1)))
                and window(e, 1)[1] in candles]
    if kind == "matched_positive":
        eligible = [e for e in eligible if e["trailing_return"] > 0 and not e["extreme"]]
    chosen = []
    for target in primary:
        when = target["signal_hour"]
        options = (event for event in eligible
                   if event["signal_hour"] != when
                   and (event["signal_hour"].year, event["signal_hour"].month) == (when.year, when.month)
                   and event["signal_hour"].hour == when.hour
                   and all(abs(event["signal_hour"] - match["signal_hour"]) >= 49 * HOUR
                           for match, _ in chosen))
        def rank(event):
            diff = (abs(event["trailing_return"] - target["trailing_return"])
                    if kind == "matched_positive" else 0)
            return diff, abs(event["signal_hour"] - when), event["signal_hour"]
        best = min(options, key=rank, default=None)
        if best is not None:
            chosen.append((best, when))
    return sorted(chosen, key=lambda pair: pair[0]["signal_hour"])


def build_trade(event, kind, delay, paired, candles, funding, oracle, valid_oracle, stress_hours):
    entry, exit_ = window(event, delay)
    entry_price = candles[entry]["open"]
    ratio = candles[exit_]["open"] / entry_price
    gross = ratio - 1
    row = dict(strategy=kind, signal_hour=event["signal_hour"], signal_settled_at=event["settled_at"],
               paired_primary_signal=paired, entry=entry, exit=exit_, entry_price=entry_price,
               exit_price=candles[exit_]["open"], gross=gross,
               funding_signal=event["funding_rate"], funding_threshold=event["first_percentile"],
               trailing_return=event["trailing_return"],
               crash_window=entry < CRASH + 2 * DAY and exit_ > CRASH,
               extreme_selloff_hours=sum(h in stress_hours for h in hours(entry, exit_)),
               minimum_held_hour_volume_eth=min(candles[h]["volume"] for h in hours(entry, exit_)))
    settlements = []
    for h in hours(entry, exit_ + HOUR):
        record = funding[h]
        actual = record["time"]
        if not entry < actual < exit_:
            continue
        oracle_index = bisect.bisect_left(valid_oracle, h) - 1
        if oracle_index < 0:
            raise ValueError("No earlier oracle observation")
        proxy_hour = valid_oracle[oracle_index]
        price = oracle[proxy_hour]["oracle_price"]
        flow = -record["funding_rate"] * price / entry_price
        item = dict(strategy=kind, entry=entry, settlement_time=actual, settlement_hour=h,
                    oracle_bucket=proxy_hour, oracle_bucket_lag_hours=(h - proxy_hour) // HOUR,
                    oracle_price=price, rate=record["funding_rate"], oracle=flow,
                    **{s: flow - d * abs(flow) for s, d in SCENARIOS.items() if s != "oracle"})
        settlements.append(item)
    row["settlements"] = len(settlements)
    row["stale_settlements"] = sum(item["oracle_bucket_lag_hours"] > 1 for item in settlements)
    row["funding_received"] = sum(max(item["oracle"], 0) for item in settlements)
    row["funding_paid"] = -sum(min(item["oracle"], 0) for item in settlements)
    funds = {key: sum(item[key] for item in settlements) for key in SCENARIOS}
    funds["zero"] = 0.
    funds["exclude_entry_hour"] = sum(item["oracle"] for item in settlements
                                     if item["settlement_hour"] > entry)
    for mode, (fee, spread, impact, adjust) in COST.items():
        row[f"{mode}_fee"] = fee * (1 + ratio)
        row[f"{mode}_spread"] = spread * (1 + ratio)
        row[f"{mode}_impact"] = impact * (1 + ratio)
        row[f"{mode}_execution"] = (fee + spread + impact) * (1 + ratio)
        row[f"{mode}_adjustment"] = adjust
        for scenario, fund in funds.items():
            net = gross - row[f"{mode}_execution"] - adjust + fund
            row[f"{mode}_{scenario}_net"] = net
            row[f"{mode}_{scenario}_equity_return"] = ALLOCATION * net
    for key, value in funds.items():
        row[f"funding_{key}"] = value
    row["funding_over_abs_gross"] = funds["oracle"] / abs(gross) if gross else None
    for mode in COST:
        net = row[f"{mode}_oracle_net"]
        row[f"funding_over_abs_{mode}_net"] = funds["oracle"] / abs(net) if net else None
        row[f"funding_over_{mode}_execution"] = funds["oracle"] / row[f"{mode}_execution"]
    return row, settlements


def mark_account(trades, candles, settlements, mode):
    fee, spread, impact, adjustment = COST[mode]
    side = fee + spread + impact
    equity = peak = 1.
    drawdown = 0.
    turnover = 0.
    for trade in trades:
        previous = equity
        entry, exit_ = trade["entry"], trade["exit"]
        allocation = previous * ALLOCATION
        ratio = trade["exit_price"] / trade["entry_price"]
        turnover += ALLOCATION * (1 + ratio)
        funding = {s["settlement_hour"]: s["oracle"]
                   for s in settlements[(trade["strategy"], entry)]}
        realized = 0.
        for h in hours(entry, exit_ + HOUR):
            mark = candles[h]["open"] / trade["entry_price"] - 1
            exit_cost = ratio * side + adjustment if h == exit_ else 0
            values = [mark - side + realized]
            if h == exit_:
                values.append(mark - side + realized - exit_cost)
            if h in funding:
                realized += funding[h]
                values.append(mark - side + realized)
            for value in values:
                marked = previous + allocation * value
                peak = max(peak, marked)
                drawdown = min(drawdown, marked / peak - 1)
        equity = previous + allocation * trade[f"{mode}_oracle_net"]
        if not math.isclose(marked, equity, abs_tol=1e-12):
            raise ValueError("Marked and closed equity differ")
        trade[f"{mode}_starting_equity_fraction"] = previous
        trade[f"{mode}_ending_equity_fraction"] = equity
        for capital in (5000, 10000):
            trade[f"{mode}_net_eur_{capital}"] = (equity - previous) * capital
            trade[f"{mode}_funding_eur_{capital}"] = allocation * trade["funding_oracle"] * capital
    return drawdown, turnover * 365 / (END - START).days


def mean(values):
    return stats.mean(values) if values else None


def account_return(trades, key):
    return math.prod(1 + ALLOCATION * trade[key] for trade in trades) - 1


def summarize(trades, candles, settlements):
    summary = dict(trades=len(trades), months={m: 0 for m in (
        "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02",
        "2026-03", "2026-04", "2026-05", "2026-06")},
        time_in_market=len(trades) / (END - START).days,
        annual_trades=len(trades) * 365 / (END - START).days,
        gross_mean=mean([x["gross"] for x in trades]),
        gross_sum=sum(x["gross"] for x in trades),
        crash_trades=sum(x["crash_window"] for x in trades),
        selloff_trades=sum(x["extreme_selloff_hours"] > 0 for x in trades),
        stale_settlements=sum(x["stale_settlements"] for x in trades),
        funding_paid=sum(x["funding_paid"] for x in trades),
        funding_received=sum(x["funding_received"] for x in trades))
    for trade in trades:
        summary["months"][trade["entry"].strftime("%Y-%m")] += 1
    summary["funding"] = {}
    for scenario in (*SCENARIOS, "zero", "exclude_entry_hour"):
        summary["funding"][scenario] = mean([x[f"funding_{scenario}"] for x in trades])
    summary["funding_abs_over_abs_gross"] = (
        sum(abs(t["funding_oracle"]) for t in trades) / sum(abs(t["gross"]) for t in trades)
        if any(t["gross"] for t in trades) else None)
    for mode in COST:
        key = f"{mode}_oracle_net"
        drawdown, turnover = mark_account(trades, candles, settlements, mode)
        result = dict(net_sum=sum(t[key] for t in trades), net_mean=mean([t[key] for t in trades]),
                      net_median=stats.median([t[key] for t in trades]) if trades else None,
                      wins=sum(t[key] > 0 for t in trades), account_drawdown=drawdown,
                      turnover_annual=turnover, scenario={}, split={},
                      funding_eur_5000=sum(t[f"{mode}_funding_eur_5000"] for t in trades),
                      funding_eur_10000=sum(t[f"{mode}_funding_eur_10000"] for t in trades))
        for component in ("execution", "fee", "spread", "impact", "adjustment"):
            result[component] = mean([t[f"{mode}_{component}"] for t in trades])
        for scenario in (*SCENARIOS, "zero", "exclude_entry_hour"):
            label = f"{mode}_{scenario}_net"
            pnl = account_return(trades, label)
            result["scenario"][scenario] = dict(mean=mean([t[label] for t in trades]),
                                                 account_return=pnl, eur_5000=pnl*5000,
                                                 eur_10000=pnl*10000)
        for label, subset in (("early", [t for t in trades if t["entry"] < MID]),
                              ("late", [t for t in trades if t["entry"] >= MID])):
            result["split"][label] = dict(trades=len(subset), mean=mean([t[key] for t in subset]),
                                          account_return=account_return(subset, key))
        if trades:
            best, worst = max(trades, key=lambda t: t[key]), min(trades, key=lambda t: t[key])
            result["best"] = dict(entry=best["entry"], net=best[key])
            result["worst"] = dict(entry=worst["entry"], net=worst[key])
            result["leave_best_out_account"] = account_return([t for t in trades if t is not best], key)
        summary[mode] = result
    return summary


def blocks(rows):
    grouped = [[0., 0] for _ in range(BLOCK_COUNT)]
    for stamp, value in rows:
        block = grouped[(stamp - START).days // 7]
        block[0] += value
        block[1] += 1
    return grouped


def bootstrap(left, right=None):
    rng = random.Random(14014)
    def aggregate(series, indices):
        total = sum(series[i][0] for i in indices)
        count = sum(series[i][1] for i in indices)
        return total / count if count else None
    draws = []
    for _ in range(10000):
        indices = rng.choices(range(BLOCK_COUNT), k=BLOCK_COUNT)
        lhs = aggregate(left, indices)
        rhs = aggregate(right, indices) if right is not None else 0.
        if lhs is not None and rhs is not None:
            draws.append(lhs - rhs)
    summary = dict(blocks=BLOCK_COUNT, kept_draws=len(draws),
                   occupied_left=sum(bool(row[1]) for row in left),
                   occupied_right=sum(bool(row[1]) for row in right) if right is not None else None)
    if draws:
        summary["interval"] = [percentile(draws, .025), percentile(draws, .975)]
    return summary


def infer(groups):
    primary = {t["signal_hour"]: t for t in groups["primary"]}
    inference = {}
    for mode in COST:
        key = f"{mode}_oracle_net"
        base_blocks = blocks((t["entry"], t[key]) for t in primary.values())
        comparisons = {"primary": bootstrap(base_blocks)}
        for kind in ("all_extreme", "negative_return"):
            control = blocks((t["entry"], t[key]) for t in groups[kind])
            lhs = mean([t[key] for t in primary.values()])
            rhs = mean([t[key] for t in groups[kind]])
            comparisons[kind] = dict(delta=lhs-rhs if lhs is not None and rhs is not None else None,
                                     **bootstrap(base_blocks, control))
        for kind in ("matched_positive", "matched_passive"):
            pairs = [(primary[t["paired_primary_signal"]], t) for t in groups[kind]]
            rows = [(p["entry"], p[key]-t[key]) for p, t in pairs]
            comparisons[kind] = dict(delta=mean([v for _, v in rows]), n=len(pairs),
                                     median_calendar_gap_hours=(
                                         stats.median(abs(p["entry"]-t["entry"]) // HOUR
                                                      for p, t in pairs) if pairs else None),
                                     **bootstrap(blocks(rows)))
        inference[mode] = comparisons
    return inference


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, default=lambda x: x.isoformat(), allow_nan=False) + "\n")


def save_csv(path, rows):
    with path.open("w", newline="") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=rows[0], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


def main():
    funding, candles, oracle, validation = validate()
    OUT.mkdir(exist_ok=True)
    save_json(OUT / "validation.json", validation)
    events = signals(funding, candles)
    primary = choose(events, "primary", candles)
    chosen = {
        "primary": [(e, None) for e in primary],
        "all_extreme": [(e, None) for e in choose(events, "all_extreme", candles)],
        "negative_return": [(e, None) for e in choose(events, "negative_return", candles)],
        "matched_positive": match(events, primary, "matched_positive", candles),
        "matched_passive": match(events, primary, "matched_passive", candles),
        "delay_2h": [(e, None) for e in choose(events, "primary", candles, delay=2)],
    }
    stress_cutoff = percentile([e["trailing_return"] for e in events], .01)
    stress_hours = {e["signal_hour"] for e in events if e["trailing_return"] <= stress_cutoff}
    valid_oracle = sorted(h for h, row in oracle.items() if row["oracle_price"] is not None)
    groups, settlement_rows, summaries = {}, {}, {}
    for kind, targets in chosen.items():
        records = []
        for event, paired in targets:
            row, flows = build_trade(event, kind, 2 if kind == "delay_2h" else 1, paired,
                                     candles, funding, oracle, valid_oracle, stress_hours)
            records.append(row)
            settlement_rows[kind, row["entry"]] = flows
        groups[kind] = records
        summaries[kind] = summarize(records, candles, settlement_rows)
    counts = dict(hourly=len(events), extreme=sum(e["extreme"] for e in events),
                  positive=sum(e["proposed"] for e in events),
                  negative=sum(e["extreme"] and e["trailing_return"] < 0 for e in events),
                  primary_dispositions=dict(Counter(e["primary"] for e in events)))
    report = dict(events=counts, stress_return_cutoff=stress_cutoff, results=summaries,
                  inference=infer(groups))
    save_csv(OUT / "events.csv", events)
    save_csv(OUT / "trades.csv", [t for kind in STRATEGIES for t in groups[kind]])
    save_csv(OUT / "settlements.csv", [s for flow in settlement_rows.values() for s in flow])
    save_json(OUT / "summary.json", report)
    print(f"Development only: {len(events)} funding events, {len(primary)} completed primary trades.")


if __name__ == "__main__":
    main()
