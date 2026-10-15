# Validation report

Verified on 2026-10-15 using Python 3.12.14 on Windows.

## Automated tests

Command: `python -m unittest discover -s tests -v`

Result: **21 tests passed**. Tests include actual loopback HTTP calls, transient HTTP retries, non-retried 404 errors, malformed CRM payloads and identity mismatches, mocked timeout budgets, full-flow metrics and evidence, stored data zones, client-isolated history, input rejection, exact/conflicting duplicates, constant and zero-MAD baselines, zero denominators, 30-day window boundaries, all human dispositions, repeated-review rejection, memory failures, exception containment, and log payload exclusion.

Regression tests also check strict YYYY-MM-DD dates and liquidity values immediately below, at, and above the three-month threshold. These tests prevent alternative ISO formats and display rounding from changing validation or risk decisions.

## Public CLI verification

Command: `python scripts/generate_samples.py`

Result: **passed** for first run, durable memory across separate processes, CRM fallback, invalid-input exit code 1, human override, retrieval of the reviewed result, and refusal of a second review. This command generated the checked-in JSON outputs and execution log using an isolated temporary database.

## Expected sample findings

- Monthly surplus: -1200 USD.
- Liquid reserve runway: 1.67 months.
- Portfolio value: 100000 USD; largest holding: 70%.
- Unusual outflow: T08, 9000 USD.
- Current/prior 30-day outflows: 9220 / 520 USD.
- Overall triage: high; initial state: pending_review.

## Scope

The tests use synthetic records and local services. The GitHub Actions workflow runs the test suite and CLI verification on Ubuntu and Windows with Python 3.11 and 3.13. Current hosted results are available on the repository's [Actions page](https://github.com/Alfhendra/wealth-advisor-assistant/actions).

The suite verifies application behavior; it does not evaluate financial calibration, model quality, production performance, or reviewer authentication.
