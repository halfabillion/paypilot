# Compliance checklist — 7 Nov 2026 (build session)

PASS here means the item is true of this repository. It is not organizer acceptance. The previous preparation pass (rules read, deadline, eligibility notes, disclosure drafts) still stands. This file records the implementation that now exists.

| Requirement | Status | Evidence / remaining work |
|---|---|---|
| Official rules read | PASS | Public rules and overview were read before this build. Deadline 12 Nov 2026 20:00 UK. |
| UK residence not excluded by the published country list | PASS | Personal eligibility, age, conflicts, rights, and terms still need Ali's confirmation. |
| No prescribed track; PayPal + AI required; MCP/Toolkit optional | PASS | General entry. No sponsor-tool award is claimed. Agent Toolkit and MCP are not used. |
| Working MVP and offline demo | PASS | `make serve` on 127.0.0.1:8765. `make demo` prints `DEMO PASS` and walks draft, send, GET status, list, payment link, capture refund, recorded refund, invalid amount, reject, and redaction. |
| Passing offline tests | PASS | `python3 -m pytest` for this public release: 74 passed, against a mock PayPal client, no live PayPal call. Covers validation, confirm-before-write, idempotency, session isolation, mock ids, redaction, sandbox request shape against a stub, 401/422 mapping, token reuse and refresh cap, fail-closed config, and the OpenAI-compatible adapter against a fixture completion. |
| Real PayPal sandbox integration, central to the product | PENDING | `SandboxPayPal` implements OAuth, Invoicing v2 create/send/get/list, a payment-link request body, a capture-refund request body, and record-refund. Live host is refused. Missing credentials refuse startup. No real sandbox call has been verified. Payment Links and capture refunds are not claimed to succeed in sandbox. |
| Meaningful AI integration | PASS for the local Qwen rehearsal. | On 7 Nov 2026 the local build was verified with Qwen2.5-3B (`qwen2.5:3b`) via Ollama. `PAYPILOT_PAYPAL_MODE` stayed `mock`. The deterministic parser remains the default for `make test` and `make demo`. |
| Approval gates, validation, idempotency | PASS | Writes wait for confirm. Invalid amounts never call the gateway. Same proposal confirm reuses the stored result. `PayPal-Request-Id` is stable per proposal. Recorded refunds are labeled as not transferring money. |
| Secret controls and `.env.example` | PASS | `.env.example` has blank secrets. `.gitignore` ignores `.env` and `data/`. Activity, health, and errors are redacted. Tests inject a canary secret and assert it never appears. No secret is committed. |
| English description and AI disclosure | PASS | `README.md` and `docs/SUBMISSION_FIELDS.md`. Disclosure: Cursor cloud agents wrote the code; Grok Bot assisted coordination, rules research, testing and documentation; local runtime AI was Qwen2.5-3B via Ollama. Deterministic intent and PayPal MOCK fixtures are test aids, not sandbox evidence. |
| Authenticated Devpost form | PENDING | Not opened. Offline fields are drafts and may miss questions on the real form. |
| Setup, run, and judge access | PASS for local MOCK. PENDING for sandbox credentials. | README setup is a venv, install, `.env`, `make test`, `make demo`, `make serve`. Judges use their own sandbox app. Access must stay free through 15 Dec 2026 16:00 UK. Do not publish the secret. |
| Public OSS repo and detectable licence | PASS | `LICENSE` is MIT and names Ali Ghaedi. Public repository: https://github.com/halfabillion/paypilot |
| Local git commit | PASS | Implementation is committed on this branch. See the git log. |
| Video script under three minutes | PASS | `docs/DEMO_SCRIPT.md`, target 2:35. Recording length is not measured because it has not been recorded. |
| Public YouTube video of the functioning project | PENDING | Needs recording of the real UI, Ali's upload, and a check that no secrets, personal data, music, or unpermitted marks are in frame. Mock footage cannot stand in for sandbox evidence. |
| Third-party licensing and originality | PASS for this tree | New application. Dependencies are MIT/BSD (FastAPI, Starlette, Uvicorn, HTTPX, Pydantic, Pytest). No PayPal SDK, no sponsor SDKs, no copied artwork, no trademarks shipped. Prior Allowance/AgentReceipts projects are not in this tree. |
| Sandbox-only, no live funds | PASS | Live PayPal hosts are rejected. Health reports `live_payments_enabled: false`. |
| Submission authorization | PENDING | Ali reviews the final form and explicitly approves submit before 12 Nov 2026 20:00 UK. |

Summary: PayPal sandbox calls are still unverified. The Devpost form, the YouTube video, and submit authorization are still open. The public MIT repository is https://github.com/halfabillion/paypilot. Tests run against a mock PayPal client. Live Sandbox mode exists and is not yet verified.

Ali still needs to: confirm personal eligibility, ownership, and terms; create the sandbox business and personal accounts and REST app and supply credentials through the process environment, not chat or git; decide judge-access arrangements; record and publish the YouTube video; review the live form; authorize submit. Publishing this repository did not include a PayPal, YouTube, or Devpost login, and it did not create those accounts or spend money.
