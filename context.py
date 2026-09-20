from dataclasses import dataclass


@dataclass(frozen=True)
class CustomerContext:
    session_id: str
    authenticated: bool
    customer_id: str | None = None
