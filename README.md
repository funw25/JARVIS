# Jarvis

For the current independent product and architecture reassessment, start with
[product scope](docs/JARVIS_V2_PRODUCT.md), [target architecture](docs/JARVIS_V2_ARCHITECTURE.md),
and [current phase and next milestone](docs/JARVIS_V2_EXECUTION.md). These are proposals;
the existing code remains a synthetic-data prototype.

A local-first personal information and action workspace: mail, tasks, captured
knowledge and conversations in one place, with a human approval step in front of
every external action.

> **Status: early development.** Jarvis runs on synthetic (fictional) data only.
> It does not connect to a real mailbox or chat account, and it does not send anything.
> A local Ollama model has only been evaluated on fictional samples and is not in the main workflow.
> Do not put real mail, chat logs, passwords, keys or other
> personal data into the current build.

---

## Contents

- [What it is](#what-it-is)
- [What it does](#what-it-does)
- [Design principles](#design-principles)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Getting started](#getting-started)
- [Verification](#verification)
- [Safety and privacy boundaries](#safety-and-privacy-boundaries)
- [Roadmap](#roadmap)
- [Documentation](#documentation)
- [License](#license)

---

## What it is

Jarvis is a workbench, not a chatbot wrapper. The goal is to take information that
is scattered across mail, saved posts, notes and messages, turn it into something
readable and actionable, and keep every decision traceable back to its source.

The project deliberately separates two concerns:

- **A domain backend** — a Python/FastAPI service with SQLite storage that models
  todos, reminders, mail, capture, knowledge, conversations, memory, approvals,
  search and audit as testable domain services.
- **A HUD interface** — a cinematic single-file prototype used to explore how that
  domain should be presented. It is currently frozen and **not** connected to the
  backend.

Everything in the repository is built around one rule: **the model proposes, the
user decides.** There is no code path that lets a generated suggestion send mail,
post content or destroy data without an explicit, per-item human approval.

## What it does

| Area | Implemented (synthetic data) | Not implemented |
|---|---|---|
| Todos | create, edit, priority, due dates, status, delete/restore, filtering, pagination, idempotent retries | real-user data protection, multi-device sync |
| Reminders | lead time, local due queries, confirmed acknowledgement, duplicate protection | OS notifications, background scheduling |
| Mail | fictional inbox, threads, filters, triage, convert to todo, reply drafts, simulated approval, rule preview/apply/revert | Gmail OAuth, real sync, real sending |
| Capture & knowledge | captures with source metadata, notes, tags, paging, search, convert to todo | browser clipper, real file/web import, semantic search |
| Conversations | threads, message history, continuity snapshots, "continue where we left off" | real model calls, cloud sync |
| Memory | namespaces, candidates, approval, forget, delete, read-only shares | key lifecycle, cross-device memory |
| Approvals | action proposals bound to a payload snapshot and hash, audit trail | real outbound execution |
| Search | cross-entity local keyword search with bounded results | server-side index, semantic search |
| Storage | schema migrations with checksums, per-migration rollback verification | encryption in the default database |
| Security | synthetic-data gate, local bearer token, Host/Origin guard, input validation | sessions, device trust, key recovery |

## Design principles

1. **Suggestions are not permissions.** A model output is an `AISuggestion`; only
   the policy and approval layer can turn it into an executed action.
2. **Local first, explicit egress.** Data lives on the device. Cloud model calls
   would be announced before they happen and limited to what the task needs.
3. **Fail closed.** Missing configuration, an unreadable key or an unknown
   migration state stops the operation instead of silently degrading.
4. **Traceability.** Summaries, classifications and drafts must point back to the
   source record they came from.
5. **Boring, testable seams.** One storage interface, injectable backends, and
   verification scripts instead of untested assumptions.

## Architecture

```
                 ┌──────────────────────────────┐
                 │  HUD prototype (preview.html)│   frozen, simulated data
                 └──────────────┬───────────────┘
                                │  (not connected yet)
                 ┌──────────────▼───────────────┐
                 │  FastAPI app (backend/app)   │
                 │  routes → services → repos   │
                 └──────────────┬───────────────┘
                                │  database.connect()
                 ┌──────────────▼───────────────┐
                 │  storage layer               │
                 │  · sqlite3 (default, gate)   │
                 │  · APSW SQLite3MC (experimental, encrypted)
                 └──────────────────────────────┘
```

The default storage is plain SQLite behind a `JARVIS_DATA_MODE=synthetic` gate.
An experimental encrypted adapter (APSW + SQLite3 Multiple Ciphers, ChaCha20, WAL,
DPAPI-wrapped key) can be selected for verification runs; it is not the default
and does not protect real data yet.

## Repository layout

```
backend/
  app/                 domain: api, services, repositories, security, migrations
  migrations/          17 versioned SQL migrations
  experimental/        encrypted adapter and isolated storage experiments
  verification/        62 checks plus standalone suites
  demo_jarvis.py       dependency-free synthetic demo
  API_REFERENCE.md     local HTTP contract
docs/                  product, architecture, security, storage and status records
preview.html           HUD prototype (dual assistant, memory, approvals)
tools/env-repro/       Windows sandbox/token diagnostics used during development
```

## Getting started

### Requirements

- Python 3.11 or newer (3.13 is the reference environment; 3.14 is used for the
  encrypted storage experiments)
- No account, token or network access is required for the demo

### Run the synthetic demo

```bash
python backend/demo_jarvis.py
```

The demo creates a temporary database, runs the migrations, exercises a small
fictional workflow and deletes the database on exit. It needs no third-party
packages.

### Run the local HTTP API

```bash
cd backend
export JARVIS_DATA_MODE=synthetic            # Windows: set JARVIS_DATA_MODE=synthetic
export JARVIS_LOCAL_API_TOKEN=<32-128 chars, [A-Za-z0-9_-]>
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Requests must come from `127.0.0.1:8765` / `localhost:8765`; the Host/Origin guard
rejects everything else. A call looks like:

```bash
curl -H "Authorization: Bearer $JARVIS_LOCAL_API_TOKEN" \
     -H "X-Jarvis-Assistant: jarvis" \
     http://127.0.0.1:8765/api/v1/todos
```

### Open the HUD prototype

Open `preview.html` in a browser (no server needed). It includes its own
self-checks:

| URL | Purpose |
|---|---|
| `preview.html?selftest=1` | 12 unit checks (memory visibility, approval hashes, filters, persistence) |
| `preview.html?e2e=1` | 12 interaction checks (navigation, drafts, approvals, sharing, send notice) |
| `preview.html?page=记忆&assistant=companion` | deep link to a page and assistant |

## Verification

All checks use fictional data and temporary databases.

```bash
backend/verification/verify_all.py            # 62 checks
python backend/verification/verify_adapter_suite.py  # same suite on the encrypted backend
```

| Environment | Result |
|---|---|
| Python 3.14, default SQLite (last recorded) | **62 script items PASS / 0 FAIL / 0 SKIP** |
| Python 3.14, encrypted adapter (last recorded) | **62 script items PASS / 0 FAIL / 0 SKIP** |
| Python 3.13, default SQLite | 46 PASS / 0 FAIL / 16 SKIP (3.14-only experiments) |

One of the 62 items is `driver_provenance`: it can be counted as PASS while
printing `G1_GATE=OPEN`. The aggregate is a synthetic regression count, not a
release gate or approval for personal data. These are previously recorded runs;
this documentation change did not rerun them.

## Safety and privacy boundaries

- **Synthetic only.** Every workflow is gated behind `JARVIS_DATA_MODE=synthetic`.
  Real mail, chat logs, credentials and personal documents are out of scope until
  storage encryption, key recovery and an independent review are complete.
- **No hidden egress.** There are no model calls, no analytics and no telemetry.
- **No account connection.** Gmail, X, WeChat and Xiaohongshu integrations are
  designed but not implemented; no OAuth client, token or cookie handling exists.
- **Secrets stay outside the repository.** Local tokens and keys are read from the
  environment and are never written to version control or logs.
- **Fail closed.** Unknown configuration, a wrong key or an inconsistent migration
  history aborts the operation.

## Roadmap

1. **Storage hardening** — key lifecycle, backup and restore-to-new-device, WAL and
   concurrency stress tests, license and release review of the encrypted driver.
2. **HUD integration** — connect the prototype to the local API and replace the
   simulated data with live domain data.
3. **Model integration** — a provider interface with explicit pre-send disclosure,
   cost tracking and no tool execution rights.
4. **Account connectors** — Gmail read-only first, then drafts and rule-based
   organisation; every outbound action stays item-by-item approved.
5. **Multi-device** — end-to-end encrypted sync, device trust, revocation and
   conflict resolution.

## Documentation

| Document | Contents |
|---|---|
| [docs/CURRENT_STATUS.md](docs/CURRENT_STATUS.md) | what works today, what does not |
| [docs/PRODUCT_REQUIREMENTS.md](docs/PRODUCT_REQUIREMENTS.md) | product scope and priorities |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | system design and module boundaries |
| [docs/DATA_SECURITY_DESIGN.md](docs/DATA_SECURITY_DESIGN.md) | local data protection plan |
| [docs/STORAGE_ADAPTER_PLAN.md](docs/STORAGE_ADAPTER_PLAN.md) | storage contract and encrypted adapter |
| [docs/SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md) | current security posture |
| [docs/ROADMAP.md](docs/ROADMAP.md) | milestones and acceptance criteria |
| [docs/DECISIONS.md](docs/DECISIONS.md) | architecture decision records |
| [docs/PROJECT_LOG.md](docs/PROJECT_LOG.md) | chronological work log |
| [backend/API_REFERENCE.md](backend/API_REFERENCE.md) | local HTTP endpoints |

## Contributing

This is a personal project in active development. Issues and design comments are
welcome; please do not include real personal data, credentials or mail bodies in
issue reports, test data or pull requests.

## License

MIT — see [LICENSE](LICENSE).
