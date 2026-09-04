# Agent-facing documents, as files

A snapshot of what the DwarPal API serves for the demo merchant, Trail & Turf, written by
`python -m dwarpal export --out data` from a freshly seeded database. Nothing here is typed by hand;
regenerate it (`make reports` does) whenever the catalog, the policy or the discovery document changes.

| File | Served at | What it is |
|---|---|---|
| `.well-known/agent-commerce.json` | `GET /.well-known/agent-commerce.json` | discovery: feed and checkout URLs, auth (bearer, plus Ed25519 request signing once an agent registers a public key), payment rails, session statuses, the policy summary an agent can plan against, and the documented deviations from ACP |
| `feed.json` | `GET /agent/v1/products` | the catalog as an agent sees it: merchant-approved fields only, priced in integer paise from the merchant's own catalog, category present only once a human approved the enrichment |
| `policy.json` | the dashboard's policy editor | the merchant policy the gate enforces: categories sold to agents, maximum order, stock rule, blocked SKUs, per-line quantity, review threshold, refund window |

URLs use the default `DWARPAL_BASE_URL` (`http://127.0.0.1:8000`); a deployed merchant regenerates with its own.
The live endpoints are authoritative: an agent should read discovery from the server, not from this folder.
