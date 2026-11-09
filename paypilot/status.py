"""Plain-language invoice status. The raw PayPal status is kept alongside it."""

STATUS_PLAIN = {
    "DRAFT": "The invoice is a draft. It has not been sent and it has not been paid.",
    "SENT": "The invoice has been sent and is unpaid.",
    "UNPAID": "The invoice is unpaid.",
    "SCHEDULED": "The invoice is scheduled. It has not been paid.",
    "PAYMENT_PENDING": "A payment is pending. It is not a completed payment.",
    "PAID": "The invoice is paid.",
    "MARKED_AS_PAID": "The invoice was marked paid outside a capture in this session.",
    "PARTIALLY_PAID": "The invoice is partially paid.",
    "PARTIALLY_REFUNDED": "A partial refund is recorded on this invoice.",
    "REFUNDED": "The invoice is refunded.",
    "MARKED_AS_REFUNDED": "The invoice is marked refunded from a recorded external refund.",
    "CANCELLED": "The invoice is cancelled.",
}


def plain_status(status: str) -> str:
    return STATUS_PLAIN.get(status, f"PayPal status is {status}.")
