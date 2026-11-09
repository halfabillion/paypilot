# Demo script — target 2:35 (must remain under 3:00)

Not recorded. Qwen2.5-3B was rehearsed on 7 Nov 2026 with PayPal in MOCK. Record a sandbox video only after a real PayPal sandbox call has been verified. Mock footage cannot stand in for sandbox evidence. No music, no unpermitted logos or third-party marks. Watch the footage for secrets and personal data.

`make demo` is always MOCK and always uses the deterministic parser. On 7 Nov 2026 the same flow was also rehearsed with Qwen2.5-3B (`qwen2.5:3b`) via Ollama, still with `PAYPILOT_PAYPAL_MODE=mock`. No real sandbox call has been verified. Do not describe payment links or capture refunds as sandbox successes. The recording itself, when it is made, has to show ids returned by PayPal before any sandbox claim.

0:00–0:15: “Freelancers should not have to switch tools to invoice clients and track payments. PayPilot turns a request into a reviewable PayPal action.” Show the mode banner that is actually on screen. A MOCK rehearsal must say MOCK. A SANDBOX banner belongs only in a recording made after a verified sandbox call.

0:15–0:45: Enter an invoice request with a sandbox test buyer, an amount, and a line item. Show the proposal, review it, confirm it, and show the sanitized invoice id from the response.

0:45–1:05: Confirm send separately. Show the PayPal-hosted invoice link. Do not imply a draft was paid.

1:05–1:25: Ask for status. Show the GET and the UNPAID or PAID value from that response. If a buyer payment is cut into the video, label the cut.

1:25–1:45: Payment Links are not verified against the PayPal sandbox. In a MOCK rehearsal, show the fixture link and say it is a fixture. In a sandbox recording, create a payment link only if that product has been verified on the REST app. Otherwise show the invoice list and say what was actually verified.

1:45–2:10: A capture refund is not the same as recording an external refund. Recording an external refund does not transfer money. Do not substitute one for the other. A MOCK rehearsal uses the seeded fixture capture and must say so. A sandbox recording needs a real sandbox capture id. Capture-refund success on the sandbox has not been verified.

2:10–2:25: Show an invalid amount rejected with no PayPal call, the confirm boundary, the activity feed, and secret redaction.

2:25–2:35: Name the model and tool that actually ran, point at the repo setup, and say how judges get access. “Sandbox only. No real funds.”

Preflight: final export at or under 2:59; public YouTube URL; repo URL checked; no tokens, keys, or buyer private details on screen; every claim matches a capability that is really implemented.
