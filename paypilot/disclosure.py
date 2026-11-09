"""Copy shown in the product. Kept here so the UI, API, and demo match."""

PRODUCT_DISCLOSURE = (
    "PayPilot asks the configured intent provider for a typed proposal. "
    "The default provider is a deterministic parser used for offline tests and demos; "
    "it is not a language model. Set PAYPILOT_INTENT_PROVIDER=openai to call an "
    "OpenAI-compatible chat model (OpenAI, Groq, a Gemini compatible endpoint, or local Ollama). "
    "The model only fills fields. PayPilot validates them and calls PayPal only after you confirm."
)

BUILD_DISCLOSURE = (
    "Code was written with AI coding assistance using Cursor cloud agents. "
    "Runtime AI: Qwen2.5-3B via Ollama converts requests into typed intent proposals. "
    "The deterministic parser and PayPal MOCK fixtures are test aids, not runtime AI or sandbox proof."
)

MOCK_BANNER = (
    "MOCK mode. PayPal calls stay on this machine and use fixtures. "
    "Ids containing MOCK, and any link on mock.paypilot.invalid, are not sandbox results."
)

SANDBOX_BANNER = (
    "SANDBOX mode. Money calls go to https://api-m.sandbox.paypal.com only. "
    "There is no live PayPal production path. Sandbox balances are test funds."
)
