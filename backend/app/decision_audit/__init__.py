"""Decision audit persistence (D2, `decision-selection-audit-contract`).

Append-only selection journal records, ports and the PostgreSQL adapter. Built
but NOT connected: no coordinator, entry consumer or API calls it, and the
existing `AuthorizerStub` neither journals nor claims candidates. See
docs/architecture/trading-intelligence-architecture.md §19.4.
"""
