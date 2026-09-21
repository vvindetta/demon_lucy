from __future__ import annotations


class EmailError(Exception):
    """A safe, user-facing mail error; never include server replies or passwords."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        retryable: bool = False,
        delivery_uncertain: bool = False,
        accepted: tuple[str, ...] = (),
        refused: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable
        self.delivery_uncertain = delivery_uncertain
        self.accepted = accepted
        self.refused = refused
