"""The one failure type the semantic layer raises.

Messages are written to be read by the language model and relayed to a person, so
they name what was wrong *and* what would have worked. A compiler that says "no"
without saying "these are the dimensions you can use here" just sends the model
back to guessing, which is the behaviour this whole subsystem exists to replace.
"""

from __future__ import annotations


class SemanticError(Exception):
    """A semantic request that cannot be honoured, with the legal alternatives.

    Raised rather than resolved-by-guessing on every ambiguity: an unknown metric,
    an unreachable dimension, two candidate join paths, a grain the time dimension
    does not support, or a definition whose binding no longer holds. Every one of
    those has a plausible-looking wrong answer available, which is exactly why
    none of them may be answered by picking one.
    """

    def __init__(self, message: str, *, alternatives: list[str] | None = None) -> None:
        self.alternatives = alternatives or []
        if self.alternatives:
            shown = ", ".join(sorted(self.alternatives)[:20])
            message = f"{message} Available: {shown}."
        super().__init__(message)
