# PayPilot hosted demo — Render free tier

**Recommendation:** Render free Web Service. No card required for the free plan. Alternatives (Fly.io free allowance, Vercel) need more packaging; Vercel is a poor fit for a long-lived FastAPI process.

This file is local prep only. Do **not** sign up, push a deploy branch, or spend money from this box. Free signup (when MMG/Ali authorises the account step) can use `batchandledger@mail.grokbot.com`.

## What to deploy

- Source of truth: public repo https://github.com/halfabillion/paypilot (`main`).
- This `run/` copy mirrors that tree plus local helper files (`sandbox_run.sh`, `render.yaml`, `Procfile`, `runtime.txt`, this doc). Do **not** push these helpers unless Ali/MMG explicitly asks; `render.yaml` / `Procfile` are safe to add later in a docs/deploy PR.

## Mode choice

| Mode | When | Banner | Notes |
|---|---|---|---|
| **MOCK (default for public demo)** | Free public URL for judges who just need a clickable UI | Must show **MOCK** | Offline fixtures. Not PayPal sandbox. Label every claim. |
| **SANDBOX** | Private or password-gated judge access after a verified local sandbox run | Must show **SANDBOX** | Set `PAYPILOT_PAYPAL_MODE=sandbox` plus `PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` in the Render dashboard env only. Never commit them. Host must stay `https://api-m.sandbox.paypal.com`. |

**Default for the first hosted deploy: MOCK, clearly labelled.** Flip to SANDBOX only after local `sandbox_run.sh` evidence exists and secrets are injected via the Render UI.

## Exact steps (Render Dashboard)

1. Sign up at https://render.com (free). Verify email.
2. New → Blueprint, or New → Web Service → connect `halfabillion/paypilot` (or paste the public GitHub URL).
3. If using the Blueprint: point at `render.yaml` once it is on the default branch. Otherwise fill the form manually:
   - **Runtime:** Python
   - **Build command:** `pip install -e .`
   - **Start command:** `uvicorn paypilot.main:create_app --factory --host 0.0.0.0 --port $PORT`
   - **Plan:** Free
   - **Health check path:** `/api/health`
4. Environment variables (MOCK demo):

   ```
   PAYPILOT_PAYPAL_MODE=mock
   PAYPILOT_INTENT_PROVIDER=mock
   PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com
   PAYPILOT_DB=/tmp/paypilot.sqlite
   PAYPILOT_RATE_LIMIT_PER_MINUTE=60
   ```

5. Deploy. Open `https://<service>.onrender.com/` and confirm the banner says **MOCK**.
6. Hit `GET /api/health` and confirm `"paypal_mode":"mock"` and `"live_payments_enabled":false`.
7. Free tier spins down after idle; the first request after sleep can take ~30–60s. Note that on Devpost / judge access docs.

## Start command (copy-paste)

```bash
uvicorn paypilot.main:create_app --factory --host 0.0.0.0 --port $PORT
```

`python -m paypilot` binds `127.0.0.1` only and is wrong for Render. Always use uvicorn with `--host 0.0.0.0` and `$PORT`.

## Optional SANDBOX on Render (after local evidence)

In the Render dashboard → Environment (not in git):

```
PAYPILOT_PAYPAL_MODE=sandbox
PAYPAL_CLIENT_ID=<from PayPal developer dashboard, sandbox app>
PAYPAL_CLIENT_SECRET=<sandbox secret>
PAYPAL_BASE_URL=https://api-m.sandbox.paypal.com
PAYPILOT_INTENT_PROVIDER=mock
PAYPILOT_DB=/tmp/paypilot.sqlite
```

Restart the service. Banner must say **SANDBOX**. If credentials are missing, PayPilot refuses to start (no mock fallback).

## Fly.io fallback (if Render signup blocks)

```bash
# Requires flyctl + free signup. Do not run from this box until authorised.
fly launch --name paypilot-demo --no-deploy
fly secrets set PAYPILOT_PAYPAL_MODE=mock
fly deploy
```

Dockerfile is not required; a simple `Procfile` + buildpack-style Python works on Render. A Dockerfile can be added later if Fly is chosen.

## Out of scope here

- No signup performed from this prep.
- No `git push` of deploy files.
- No live PayPal hosts.
- No Devpost submit / attestations.
