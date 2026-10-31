"""Exception types. Messages are safe to show to the model and the user (never contain secrets)."""


class AgentCartError(Exception):
    """Base class for all expected, user-facing errors."""


class UnsafeURLError(AgentCartError):
    """The URL is not allowed (scheme, host, private address, size...)."""


class VaultError(AgentCartError):
    """The vault is missing, locked, corrupted or the passphrase is wrong."""


class PolicyViolation(AgentCartError):
    """A spending rule blocked the action. Never retried automatically."""


class TamperError(AgentCartError):
    """Integrity check failed (bad signature, broken audit chain). Fails closed."""


class CheckoutError(AgentCartError):
    """The site adapter could not complete a checkout step."""
