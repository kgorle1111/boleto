# E4B config sweep — 2026-07-19 (printed 8-ticket benchmark, shipping path)

| K | T | pay-exact | critical | escaped | pay-flag rate | any-flag rate | calls | s/ticket |
|---|---|---|---|---|---|---|---|---|
| 2 | 0.2 | 52/52 (100.0%) | 0.0 | 0.0 | 0.0 | 1.0 | 16 | 4.93 |
| 3 | 0.2 | 52/52 (100.0%) | 0.0 | 0.0 | 0.0 | 1.0 | 24 | 6.93 |
| 4 | 0.2 | 52/52 (100.0%) | 0.0 | 0.0 | 0.0 | 1.0 | 32 | 9.56 |
| 2 | 0.1 | 52/52 (100.0%) | 0.0 | 0.0 | 0.0 | 1.0 | 16 | 5.15 |
| 2 | 0.0 | 52/52 (100.0%) | 0.0 | 0.0 | 0.0 | 1.0 | 16 | 4.73 |
| 4 | 0.0 | 52/52 (100.0%) | 0.0 | 0.0 | 0.0 | 1.0 | 32 | 8.94 |

Selection rule: lowest escaped-rate first, then lowest pay-flag rate (automation), then fewest calls (latency/energy). kn: printed tickets — re-run this sweep on the real handwritten set before trusting it there.