# Synthetic strategy history baseline

The growing-history regression builds validated BTTS evidence locally and uses the
strategy API with `scope=history`, `market=BTTS`, and a five-row page. It checks
all pages at 10, 50 and 100 best-value publications, including first, middle and
last pages, against the sorted validated publication records. It checks full
filtered totals and that reading does not alter saved evidence.

For a generic local measurement attempt with the project interpreter and installed
dependencies, the following command can be run from the repository root. It does
not exactly reproduce the uvloop worker run documented below:

```sh
STRATEGY_HISTORY_MEASURE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -m unittest -q tests.test_pick_strategies.BttsStrategyTests.test_growing_history_pages_cover_filtered_publications
```

The command above uses the default TestClient backend and has not been verified
in the isolated worker; the observed worker environment requires the separate
uvloop adapter described below.

The optional output records response body bytes and elapsed `time.perf_counter`
seconds around each first, middle and last TestClient request, plus cumulative
test time at each size. One execution yields one sample per page and size; test
time includes fixture creation, publication, previous sizes and all page reads.
There are no time thresholds.

## Measurement attempt

On 2026-11-13 in the isolated Linux x86_64 checkout, system Python 3.12.3 had
neither the project `.venv` nor the required installed `pydantic` and `fastapi`
dependencies. The focused suite was attempted with
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest -q tests.test_pick_strategies`;
it failed during import on missing `pydantic`. No fixture or request ran.
Sample count: **0**. Response byte sizes and read/test durations at all three
sizes were **unavailable from that attempt**. No packages were installed during
that attempt. A later partial measurement is documented below.

These synthetic, in-process local measurements would include TestClient and local
filesystem overhead and depend on fixture content and machine conditions; they
cannot establish production latency, throughput, or behavior with real history.

## Retained partial measurement (2026-11-14)

A single synthetic, in-process run used the isolated Linux x86-64 worker test
environment, Python 3.12 and uvloop 0.23.0, selected with a process-local
TestClient backend adapter. Worker networking was disabled. These results are
not production measurements and cannot establish production latency or throughput.
Each available value is one sample; no distribution or repeatability is implied.

| History records | Page offset | Response bytes | Elapsed seconds |
| ---: | ---: | ---: | ---: |
| 10 | 0 | 10,393 | 0.155014 |
| 10 | 5 | 10,396 | 0.088194 |
| 50 | 0 | 10,398 | 1.354880 |
| 50 | 25 | 10,399 | 1.516173 |
| 50 | 45 | 10,401 | 1.556629 |
| 100 | 0 | 10,405 | 4.779099 |

At 10 records, middle and last select the same offset 5. The 100-record
middle and last offsets (50 and 95) were not recorded, nor was the cumulative
100-record checkpoint. The recorded cumulative checkpoints were 0.780920 seconds
at 10 and 19.805604 seconds at 50; these include fixture construction and prior
work, not just page reads. The process exited 139 after 65.206 seconds, before
the remaining measurements. Retained evidence does not establish the crash cause.
Cleanup was verified. A separate run of 12 focused tests passed under uvloop;
that result does not establish a passing default-asyncio run. The earlier
missing-dependency attempt above is historical, not a description of the later
uvloop environment.
