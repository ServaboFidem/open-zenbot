"""Secret references.

Keys never live in files in the repo and never appear in logs. A caller
holds a reference string and resolves it at use time:

- ``env:NAME``    read environment variable NAME
- ``vault:NAME``  read a vault handle. Day one, a vault handle resolves
                  from the environment variable ``VAULT_NAME``. A real
                  vault backend replaces `_vault_lookup` later.

The resolved value is wrapped in `Secret`, whose repr and str never show
the value, so an accidental log line shows ``Secret(***)``.
"""

from __future__ import annotations

import os


class SecretNotFound(LookupError):
    pass


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str):
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    def __str__(self) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


def _vault_lookup(name: str) -> str | None:
    return os.environ.get(f"VAULT_{name}")


def resolve_secret(ref: str) -> Secret:
    scheme, _, name = ref.partition(":")
    if not name:
        raise ValueError(f"secret ref must look like env:NAME or vault:NAME, got {ref!r}")
    if scheme == "env":
        value = os.environ.get(name)
    elif scheme == "vault":
        value = _vault_lookup(name)
    else:
        raise ValueError(f"unknown secret scheme {scheme!r} in {ref!r}")
    if not value:
        raise SecretNotFound(f"secret {ref!r} is not set")
    return Secret(value)
