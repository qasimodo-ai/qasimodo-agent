"""
NKey management for the agent.

Handles User NKey generation and persistence for NATS JWT authentication.
"""

from __future__ import annotations

from pathlib import Path

import nkeys
from nacl.signing import SigningKey

# Agent NKey storage directory
AGENT_NKEY_DIR = Path.home() / ".qasimodo-agent"
USER_SEED_FILE = AGENT_NKEY_DIR / "user.seed"


def ensure_user_nkey() -> tuple[str, str]:
    """
    Generate or load User NKey for this agent.

    The User NKey is used for NATS JWT authentication. The public key is sent
    to the core API during authentication, and the seed is used to sign NATS
    connection challenges.

    Returns:
        (user_public_key, user_seed)
    """
    AGENT_NKEY_DIR.mkdir(parents=True, exist_ok=True)

    if USER_SEED_FILE.exists():
        user_seed = USER_SEED_FILE.read_text().strip()
        kp = nkeys.from_seed(user_seed.encode())
        user_public = kp.public_key.decode()
        return user_public, user_seed

    # Generate new User NKey
    signing_key = SigningKey.generate()
    seed_bytes = nkeys.encode_seed(signing_key.encode(), nkeys.PREFIX_BYTE_USER)
    kp = nkeys.from_seed(seed_bytes)
    user_seed = seed_bytes.decode()
    user_public = kp.public_key.decode()

    # Save seed (IMPORTANT: protect this file!)
    USER_SEED_FILE.write_text(user_seed)
    USER_SEED_FILE.chmod(0o600)  # Only owner can read

    return user_public, user_seed


def get_user_seed() -> str:
    """
    Get the User NKey seed (for NATS connection signature callback).

    Returns:
        User seed string
    """
    if not USER_SEED_FILE.exists():
        _, seed = ensure_user_nkey()
        return seed
    return USER_SEED_FILE.read_text().strip()


def get_user_public_key() -> str:
    """
    Get the User NKey public key (to send to core API).

    Returns:
        User public key string (starts with 'U')
    """
    public_key, _ = ensure_user_nkey()
    return public_key


__all__ = ["ensure_user_nkey", "get_user_seed", "get_user_public_key"]
