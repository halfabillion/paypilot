# Offline submission field drafts

Not submitted. The authenticated Devpost form was not opened and may have more required questions. Paste only claims that match the build.

**Title:** PayPilot

**Tagline:** Reviewable AI bookkeeping proposals. PayPal runs only after you confirm. Sandbox evidence is invoice create, send, and status GET on 9 Nov 2026. Payment Links, capture/refund, live mode, and the LLM intent provider are not verified.

**Audience / problem:** Freelancers lose the thread when creating invoices, checking whether they were paid, and reconciling refunds across separate screens.

**What it does:** PayPilot turns a plain-language request into a typed proposal. The user reviews and confirms writes. When `PAYPILOT_PAYPAL_MODE=sandbox`, the PayPal sandbox response is the invoice and payment state. On 9 Nov 2026 at 23:14 UK that path created invoice `INV2-SN87-YHFB-FLLW-TYW3` (PayPal 201), sent it (200, `SENT`), and read status (200, `SENT`) through the PayPilot app API. The intent parser on that run was mock. A redacted activity feed shows the method, path, status code, and resource id. The default `MOCK` mode runs the same flow offline with fixtures that are labeled mock. A recorded invoice refund is bookkeeping and does not transfer money. A capture refund is a different call, `POST /v2/payments/captures/{id}/refund`. Neither refund path nor Payment Links is verified on the sandbox.

**Inspiration:** Make routine invoicing easier without letting an assistant send an invoice or refund a payment on its own.

**How we built it:** Python 3.12, FastAPI, HTTPX, SQLite, a single static page. `PayPalGateway` has `MockPayPal` and `SandboxPayPal`. `IntentProvider` has a deterministic parser and an OpenAI-compatible chat adapter. Writes require confirmation. Amounts are decimals. Idempotency uses `PayPal-Request-Id`.

**AI tools used and how:** Code was written with AI coding assistance using Cursor cloud agents. Grok Bot assisted coordination, rules research, testing and documentation. Runtime AI verified locally: Qwen2.5-3B via Ollama. Deterministic intent and PayPal MOCK fixtures are test aids, not genuine AI or sandbox evidence. The 7 Nov 2026 rehearsal used `qwen2.5:3b` with `PAYPILOT_PAYPAL_MODE=mock`. The 9 Nov 2026 sandbox run used the mock intent parser (no LLM key), so the LLM intent provider is not verified. MOCK is not PayPal sandbox or live PayPal.

**PayPal integration:** The client implements OAuth `POST /v1/oauth2/token` and Invoicing v2 create, send, get, and list. It also builds a Payment Links `POST /v1/checkout/payment-resources` body and a Payments v2 capture-refund body, plus an invoicing record-refund body. Live PayPal hosts are refused. Verified live-sandbox on 9 Nov 2026 at 23:14 UK, against `https://api-m.sandbox.paypal.com`: create `POST /v2/invoicing/invoices` (201, `INV2-SN87-YHFB-FLLW-TYW3`), send (200, `SENT`), status GET (200, `SENT`). Do not claim that payment links, capture refunds, invoice list, recorded refunds, live mode, or the LLM intent provider succeeded in the sandbox.

**Built with:** Python, FastAPI, HTTPX, SQLite, a PayPal REST sandbox client (invoice create, send, and status GET verified on the sandbox on 9 Nov 2026; Payment Links, capture/refund, live mode, and the LLM intent provider are not), Qwen2.5-3B via Ollama. Do not tag unused sponsor tools.

**Challenges:** Keeping mock fixtures obviously fake, splitting “record a refund” from “refund a capture”, and refusing to treat a deterministic parser as the AI requirement. Sandbox success is claimed only for the 9 Nov 2026 invoice create, send, and status GET.

**What's next:** Production authorization, webhooks, and reconciliation. None of those are in this build.

**Existing project:** New application written for the submission window. Allowance and AgentReceipts are not in this tree.

**Repository URL:** https://github.com/halfabillion/paypilot (public). MIT licence is in `LICENSE`. Devpost submission still needs Ali's approval.

**Demo URL:** https://paypilot-ozt7.onrender.com — MOCK mode demo (offline fixtures, not PayPal). Local server: `make serve` → `http://127.0.0.1:8765`.

**Video URL:** Pending a public YouTube upload under three minutes. Script: `docs/DEMO_SCRIPT.md`.

**Testing instructions:** `python3 -m venv .venv && source .venv/bin/activate && python -m pip install -e ".[dev]" && make test && make demo && make serve`. `make test` and `make demo` run in MOCK mode. `make demo` always uses the deterministic parser and PayPal MOCK. For Qwen plus MOCK: `ollama pull qwen2.5:3b`, then `PAYPILOT_PAYPAL_MODE=mock PAYPILOT_INTENT_PROVIDER=openai OPENAI_BASE_URL=http://127.0.0.1:11434/v1 OPENAI_MODEL=qwen2.5:3b OPENAI_API_KEY= python -m paypilot.demo --use-configured-intent`. That command still forces PayPal MOCK. For sandbox, set `PAYPILOT_PAYPAL_MODE=sandbox` and inject `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` into the process environment. Do not commit them or paste them into the form. The verified sandbox run is invoice create, send, and status GET on 9 Nov 2026 (`docs/SANDBOX_VERIFICATION.md`). Payment Links, capture/refund, live mode, and the LLM intent provider are not verified. Keep judge access free through 15 Dec 2026 16:00 UK.

**Track / prize:** No prescribed tracks. General entry. No sponsor-tool award claimed.

**Team:** Solo. Ali Ghaedi confirms the representative name, eligibility, rights, conflicts, and terms.

**Final submission:** Pending Ali's explicit approval. Deadline 12 Nov 2026 20:00 UK.
