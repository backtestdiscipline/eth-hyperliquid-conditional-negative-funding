# Discovery 014 development specification

## Hypothesis and partition

Native Hyperliquid ETH perpetual longs following extreme negative settled
funding and a positive preceding 24-hour return have positive 24-hour net
expectancy and outperform matched positive-return windows without extreme
funding. Shek's preprint motivates the hypothesis. The exchange, frequency
and execution lag make this a transfer test.

All timestamps are UTC. Warm-up starts 2025-05-25. Development is
[2025-09-01 00:00, 2026-07-01 00:00). The reserve begins at that exclusive
end and runs through 2026-10-31. Every execution timestamp must be strictly
before the development end. The reserve supplies no market observations.

## Signal and execution

For each funding record at its actual timestamp t in nominal UTC hour h,
compute the linear-interpolated empirical first percentile on the
timestamp interval [t − 90 days, t). Require a negative settled rate at
or below the percentile. The price filter compares the close of the
bar opened at h − 1h to the close of the bar opened at h − 25h. Both
bars have completed by h. Require a strictly positive return. Use only
records already observable at t. A position or ongoing cooldown blocks
the signal. Execute at the trade-derived opening price of h + 1h and
close at the opening price 24 hours later. The cooldown permits a new
signal from exit + 24h. Reject windows with missing inputs or execution
candles. Treat opens as modeled fills at their bar timestamps.

The diagnostic uses h + 2h for entry and preserves the other rules.

## Cash flows

Size each long to 40% of tracked pre-entry equity and hold the implied ETH
quantity constant through exit. Results per entry notional are independent
of starting account size. Account illustrations use the compounded return
of the explicitly tracked equity and constant EUR conversion. There is no
FX return, borrow or additional leverage. Fees are 4.5 bp on each side's
unadjusted notional. Spread and impact are symmetric per side and charged
on the same open-price notionals. The total rates at unchanged price are
3 and 8 bp base, and 6 and 24 bp stress. Apply the extra 5 bp stress
funding debit to entry notional on trade completion. Prices for signal,
fills and hourly account marking come from traded perpetual candles.

Use only actual settlement timestamps strictly between the modeled entry
and exit timestamps. A settlement a few milliseconds into the entry hour
is therefore included by the modeled clock. Also report the diagnostic
excluding entry-hour settlements to expose sensitivity to the first fill's
ordering. For a settlement in nominal hour s use the last retained oracle
observation in the previous bucket s − 1h as the nearest prior hourly
snapshot. If that observation is missing, use the most recent earlier
retained snapshot and flag its bucket, age and affected cash flow in the
ledger. This is an explicit stale-price sensitivity input and leaves the
missing observations missing. The oracle is used only for realized funding. For fixed ETH
quantity q compute each long cash flow as −q × settled rate × proxy.
Scenario zero removes only historical funding. In each adverse scenario
replace each cash flow C by C − d × abs(C), with d of 1%, 3% and 10%.
The symmetric favorable 10% scenario is diagnostic for reversal risk.
These are hypothetical scenarios. Their percentages are not historical
coverage bounds. Mark-to-market drawdown uses modeled cash flows and hourly
opens, with entry and exit costs applied at their modeled times.

## Controls and inference

1. All extreme negative events, with the same long entry and cooldown.
2. Extreme negative events with a strictly negative prior return, with
   the same long entry and cooldown.
3. Positive return and no funding extreme, paired without replacement with
   primary episodes. Restrict to the same signal month and UTC hour of day.
   In primary chronological order minimize the difference in trailing return,
   then absolute calendar distance, then earlier timestamp.
4. Passive calendar-matched exposure. Assign one ETH long window per primary
   episode where possible, using the same signal month and UTC hour of day,
   closest in calendar time, ties to the earlier timestamp. Select without
   reference to return direction or funding. Exclude the identical primary
   window from both pairing exercises. Report gross passive exposure and its
   active-cost equivalent, and show gross on the exact primary windows
   as the directional attribution baseline.

In both matching exercises impose the same holding period and cooldown
between selected comparison windows. Pairing uses pre-signal inputs and UTC
timestamps. Its retrospective assignment is a comparison, with no role in
primary entries. Report any unmatched episodes and match distances.
Compare metrics per completed episode as well as total account economics.

Resample consecutive fixed seven-day UTC blocks starting at development start,
including empty blocks, using 10,000 draws and seed 14014. Report descriptive
95% percentile intervals for mean primary base and stress net, the independent
portfolio comparisons with controls 1 and 2 using the same resampled calendar
blocks, and matched differences with controls 3 and 4 assigned to primary entry
blocks. Sparse events and dependence beyond seven days limit this inference.
Report September–January versus February–June, leave-best-trade-out,
the Oct 10–11 2025 UTC stress window, and primary exposure to hourly
observations in the bottom 1% of development ETH trailing 24-hour returns.
The split and stress diagnostics use development data only.

Use the published decision rules. Compare adverse and favorable oracle
perturbations with price return, actual modeled costs and remaining margin.
Treat changes to the economic verdict as decision-critical, economically
meaningful changes to the margin without a verdict reversal as material
but bounded, and changes small in those comparisons as economically
immaterial within the scenario range.
