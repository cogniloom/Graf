"""Durable local investigations and independently verifiable offline packages.

Lazy exports keep the standalone verifier usable without the POSIX vault runtime
and avoid importing its module before ``python -m ...verifier`` executes it.
"""

__all__ = ["Vault", "verify_package"]


def __getattr__(name):
    if name == "Vault":
        from .vault import Vault

        return Vault
    if name == "verify_package":
        from .verifier import verify_package

        return verify_package
    raise AttributeError(name)
