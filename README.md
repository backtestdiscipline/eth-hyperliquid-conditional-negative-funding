# ETH perpetual funding cash-flow sensitivity

This companion to [How much did the missing funding price matter?](https://backtestdiscipline.com/notes/how-much-did-the-missing-funding-price-matter/) contains the original development evaluator, the [test specification](SPEC.md), and the [aggregate result](results/summary.json). The fixed Hyperliquid ETH rule produced five completed modeled trades between September 2025 and June 2026. All five lost on price alone. The decision was to stop at development.

The code, specification and aggregate result match the archived study revision `5be0eff9a835f1797adf37e0735daaaf5c181b11`. The aggregate result uses decimal fractions for returns unless a field specifies another unit. The repository includes the aggregate report and its best and worst trade diagnostics. Original market-data files and the complete trade and settlement ledgers stay in the study archive.

## Inspect or replay

The evaluator runs on Python 3.11 or newer. The synthetic timing checks do not require market data.

```sh
python3 -m unittest -v test_study.TimingTests
```

For a development replay, the original acquisition script uses PyArrow 26.0.1 on Python 3.12. It fetches the pinned archive releases and requests a single dated sample from the official Hyperliquid API. The official sample may age out of the API. In that case a fresh acquisition cannot complete with this package alone. The aggregate result remains available for inspection.

```sh
uv run --no-project --python 3.12 --with pyarrow==26.0.1 python acquire.py
python3 study.py
python3 -m unittest -v
```

Run the replay in a separate checkout if you want to retain the supplied aggregate result bytes. The evaluator writes its outputs to `results/`. The acquisition bounds end before the reserved July–October 2026 period.

## Data attribution

The [funding](https://huggingface.co/datasets/ironflow/hyperliquid-funding), [candle](https://huggingface.co/datasets/ironflow/hyperliquid-candles-1h), and [oracle](https://huggingface.co/datasets/ironflow/hyperliquid-open-interest) archives are credited to Ironflow (ironflow.sh). The releases pinned in `acquire.py` are licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The study filtered them to native ETH perpetual rows, selected the required fields and aggregated modeled trades for the published result.
