# PayPilot SANDBOX run evidence

Redacted summary of the 9 Nov 2026 sandbox run. The artifact files named below are not in this repository. This text contains no PayPal client id, client secret, or bearer token.

- UK time: 2026-11-09 23:14:48 GMT
- Mode: SANDBOX
- PayPal host: https://api-m.sandbox.paypal.com
- Local API: http://127.0.0.1:18765
- Session: ses_28df82ea19a494f3
- Invoice ID: INV2-SN87-YHFB-FLLW-TYW3
- Create activity HTTP: 201
- Send activity HTTP: 200
- Status after send (from assistant text): SENT
- Status GET activity HTTP: 200
- Authoritative status (from assistant text): SENT
- Refund: NOT run (requires a real sandbox capture id after buyer payment)
- Payment Links: NOT run (capability unverified on this REST app)
- Secrets: redacted from all JSON/log artifacts; Authorization headers scrubbed
- Live PayPal host check: aborted if api-m.paypal.com appeared without sandbox

## Files

- `run.log` — timestamps and step outcomes
- `01_health.json` … `09_session_final.json` — redacted API responses
- `*.meta.txt` — per-step HTTP codes and timestamps
- `server.log` — redacted server stdout/stderr
