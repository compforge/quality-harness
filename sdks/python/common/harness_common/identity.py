"""Shared contract for objects referenced by a string identifier."""

from typing import Protocol


class Identity(Protocol):
    """Expose an identifier whose format and uniqueness scope belong to the model.

    @spec The same business identity yields the same identifier; descriptive metadata
    does not participate in it.
    """

    def id(self) -> str: ...
