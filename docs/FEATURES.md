# Feature coverage

This map connects the application's behavior to its implementation and verification. Test cases are in [`tests/test_system.py`](../tests/test_system.py); the [validation report](VALIDATION.md) describes how to run them.

| Feature | Implementation | Verification |
|---|---|---|
| Financial JSON ingestion and validation | `FinancialDataTool.prepare` in `analytics.py` | Required fields, types, money, currency, strict dates and duplicate transactions |
| CRM context | `MockCRMTool`, `HttpCRMTool` and the local `mock_crm` server | Loopback HTTP success, 404 and transient errors; response schema, client identity and timeout tests |
| Explainable analysis | `RuleAnalysisTool` in `analytics.py` | Cash flow, liquidity, concentration, unusual outflows and spending changes; evidence and boundary cases |
| Central orchestration | `Orchestrator.run` in `agents.py` | Full workflow, invalid input, unexpected exceptions and persistence failures |
| Specialist agents | `DataFetcherAgent`, `AnalyzerAgent`, `AdvisoryAgent` | Fetcher → Analyzer → Advisory execution with shared session context |
| Replaceable tool adapters | Protocols in `contracts.py`; dependency injection in `__main__.py` | Mock, HTTP and failure-injection adapters used by the tests |
| Per-run state | `Session` raw, cleaned and curated records | Full-flow test verifies persisted data zones and analysis evidence |
| Durable memory | `SQLiteMemory.previous` and `save` in `tools.py` | History reuse, client isolation and memory failure tests; separate CLI processes |
| Human review | CLI `review` and `show` commands; SQLite review transaction | Approve, reject and override; evidence preservation; invalid and repeated reviews |
| Recoverable failures | CRM and history fallback paths | Unknown CRM context and missing historical context remain explicit in results |
| Structured output | Versioned JSON with metrics, signals, limitations, review state and trace | Generated examples in [`samples/`](../samples/) and CLI verification |
| Structured logging | JSONL events with timestamps, run IDs and stage names | Log payload exclusion test and [`samples/execution.jsonl`](../samples/execution.jsonl) |
| Portable local execution | Python 3.11+ and standard-library dependencies | Test and sample-generation workflow for Ubuntu and Windows |

The [README](../README.md) covers setup, commands, data contracts, analysis rules and trade-offs. [Architecture decisions](ARCHITECTURE.md) describes the data flow, state transitions and dependency boundaries.
