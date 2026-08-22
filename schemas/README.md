# `schemas/` — Pydantic API models

One module, `api.py`: the request/response models for the FastAPI gateway.
Four families live here — the core query surface (`/query`, sources, health,
soul evolution), the `/ops/*` subprocess-control models, the `/auth/*`
session/RBAC models, and the `/memory/*` propose/apply models. Every model is declared with
`extra='forbid', strict=True` — unexpected fields and loose type coercion are
rejected at the schema boundary (HTTP 422) before any retrieval or LLM work
runs, which is a deliberate hardening choice against silent data injection in
agentic flows, not a style preference.

`QueryRequest` also carries a `max_length` cap as an independent DoS
backstop, separate from the configurable injection-filter length cap in
`utils/sanitizer.py` — the two limits are layered on purpose.

Changing a field name, a bound, or an error shape here is an API-contract
change: `tests/test_gate.py` and the console contract test
(`tests/test_terminal_contract.py`) assert against these models' behavior.

## Related

- Route table these models serve: `CLAUDE.md` §2 "All HTTP routes"
- Sanitizer (the other half of input validation): `utils/sanitizer.py`
