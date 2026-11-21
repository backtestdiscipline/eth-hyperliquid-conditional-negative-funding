"""Stage only the pinned Discovery 014 warm-up and development archive months."""
import concurrent.futures
import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

import pyarrow as pa
import pyarrow.parquet as pq

DATA = Path(__file__).resolve().parent / "data"
WARMUP = datetime(2025, 5, 25, tzinfo=UTC)
START = datetime(2025, 9, 1, tzinfo=UTC)
END = datetime(2026, 7, 1, tzinfo=UTC)
DEVELOPMENT = ("2025-09", "2025-10", "2025-11", "2025-12",
               "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")
WARMUP_MONTHS = ("2025-05", "2025-06", "2025-07", "2025-08")
SOURCES = {
    "funding": ("hyperliquid-funding", "b2153cf3aed73b6b200a378064538a2263923e9d",
                ("funding_rate", "premium"), WARMUP_MONTHS + DEVELOPMENT),
    "candle": ("hyperliquid-candles-1h", "5177a7f728a968252128ba435ae142bc241dd13e",
               ("open", "high", "low", "close", "volume", "trades"),
               WARMUP_MONTHS + DEVELOPMENT),
    "oracle": ("hyperliquid-open-interest", "0e5ed2382c673210080e5e8aa5d65b3da62b4bb5",
               ("oracle_price",), DEVELOPMENT),
}
INFO = "https://api.hyperliquid.xyz/info"
JUNE_HASHES = {
    "funding": "467131e7320a3d568d289d6d10fcee3f4a0e1dde13b8a73bb04977035ab01678",
    "candle": "1aabd11fdad5eadb7db7c7779c859ec6294d7c69e97b1f0782175b035036d4af",
}


def download(job):
    kind, month = job
    repo, revision, measurements, allowed = SOURCES[kind]
    if month not in allowed:
        raise ValueError(f"Out-of-scope archive: {month}")
    url = f"https://huggingface.co/datasets/ironflow/{repo}/resolve/{revision}/data/{month}.parquet"
    with urlopen(url + "?download=true", timeout=40) as response:
        raw = response.read()
    digest = hashlib.sha256(raw).hexdigest()
    if month == "2026-06" and kind in JUNE_HASHES and digest != JUNE_HASHES[kind]:
        raise ValueError(f"Prior June {kind} hash does not match")
    source = pa.BufferReader(raw)
    metadata = pq.ParquetFile(source).metadata
    columns = ("timestamp", "market", "coin", "dex", *measurements)
    if kind == "candle":
        columns += ("market_class",)
    table = pq.read_table(source, columns=list(columns), filters=[("market", "=", "ETH-PERP")])
    rows = table.to_pylist()
    if any(row["coin"] != "ETH" or row["dex"] != "" or
           row.get("market_class", "perp") != "perp" or
           row["timestamp"].utcoffset().total_seconds() != 0 or
           row["timestamp"].strftime("%Y-%m") != month or row["timestamp"] >= END
           for row in rows):
        raise ValueError(f"Unexpected instrument, timezone, or period in {url}")
    retained = [row for row in rows if row["timestamp"] >= WARMUP]
    record = {
        "series": kind, "month": month, "file": f"{month}.parquet", "url": url,
        "sha256": digest, "archive_bytes": len(raw), "archive_rows": metadata.num_rows,
        "eth_file_rows": len(rows), "eth_retained_rows": len(retained),
        "utc_first": min(row["timestamp"] for row in retained).isoformat(),
        "utc_last": max(row["timestamp"] for row in retained).isoformat(),
        "projected_schema": {field.name: str(field.type) for field in table.schema},
    }
    return record, retained


def official_samples():
    """June 24 UTC only. No unbounded requests or fallback dates."""
    start, end = 1782259200000, 1782345599999
    queries = {
        "funding": dict(type="fundingHistory", coin="ETH", startTime=start, endTime=end),
        "candle": dict(type="candleSnapshot", req=dict(coin="ETH", interval="1h",
                                                     startTime=start, endTime=end)),
    }
    evidence = {}
    for kind, payload in queries.items():
        request = Request(INFO, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=25) as response:
            raw = response.read()
        rows = json.loads(raw)
        timestamp = "time" if kind == "funding" else "t"
        instrument = "coin" if kind == "funding" else "s"
        if len(rows) != 24 or any(not start <= row[timestamp] <= end or
                                  row[instrument] != "ETH" for row in rows):
            raise ValueError("Unexpected official ETH sample")
        evidence[kind] = dict(url=INFO, request=payload,
                              response_sha256=hashlib.sha256(raw).hexdigest(), rows=rows)
    return evidence


def main():
    jobs = [(kind, month) for kind, source in SOURCES.items() for month in source[3]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        downloaded = list(pool.map(download, jobs))
    DATA.mkdir(exist_ok=True)
    manifest = dict(license="CC BY 4.0", license_url="https://creativecommons.org/licenses/by/4.0/",
                    attribution="Ironflow (ironflow.sh)",
                    scope=dict(warmup_start=WARMUP.isoformat(), development_start=START.isoformat(),
                               exclusive_end=END.isoformat()),
                    archives=[record for record, _ in downloaded], retained_sha256={})
    for kind, (_, _, fields, _) in SOURCES.items():
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, ("timestamp", *fields), extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        rows = [row for record, subset in downloaded if record["series"] == kind for row in subset]
        writer.writerows(sorted(rows, key=lambda row: row["timestamp"]))
        path = DATA / f"{kind}.csv"
        path.write_bytes(buffer.getvalue().encode())
        manifest["retained_sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    path = DATA / "official-june24.json"
    path.write_text(json.dumps(official_samples(), indent=2) + "\n")
    manifest["retained_sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Retained ETH projections from {len(jobs)} pinned UTC development/warm-up files.")


if __name__ == "__main__":
    main()
