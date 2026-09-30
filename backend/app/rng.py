"""Provably-fair RNG.

Model (the same one Stake, Roobet and most licensed crypto casinos publish):

  * The server generates a secret `server_seed` and publishes
    `sha256(server_seed)` BEFORE any bet - the commitment.
  * The player supplies/keeps a `client_seed` they can change at any time.
  * Every bet consumes the next `nonce`.
  * Outcomes are `HMAC_SHA256(server_seed, f"{client_seed}:{nonce}:{cursor}")`,
    sliced into 32-bit uniform floats.
  * When the player rotates their server seed the old seed is revealed, so they
    can recompute every past outcome and confirm nothing was changed.

This module is pure - no DB, no I/O - so it is trivially unit-testable and the
same code can be shipped to the client for independent verification.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

FLOATS_PER_HMAC = 8
_MAX_MULTIPLIER = 1_000_000.0  # sanity cap so the curve can't print infinity


def new_server_seed() -> str:
    return secrets.token_hex(32)


def new_client_seed() -> str:
    return secrets.token_hex(8)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def commit(server_seed: str) -> str:
    """The public commitment: hash of the seed, published before betting."""
    return sha256_hex(server_seed)


@dataclass(frozen=True)
class ProvablyFair:
    """Deterministic stream of uniform floats for one (seed, seed, nonce) triple."""

    server_seed: str
    client_seed: str
    nonce: int
    cursor: int = 0
    _cache: list[float] = field(default_factory=list, compare=False, repr=False)

    def floats(self, count: int) -> list[float]:
        out: list[float] = []
        block = 0
        while len(out) < count:
            digest = hmac.new(
                key=self.server_seed.encode(),
                msg=f"{self.client_seed}:{self.nonce}:{self.cursor + block}".encode(),
                digestmod=hashlib.sha256,
            ).digest()
            for i in range(FLOATS_PER_HMAC):
                chunk = digest[i * 4 : i * 4 + 4]
                out.append(int.from_bytes(chunk, "big") / 2**32)
            block += 1
        return out[:count]

    def float(self, cursor: int = 0) -> float:
        return self.floats(cursor + 1)[cursor]

    # -- derived distributions ---------------------------------------------
    def shuffle(self, items: list, *, offset: int = 0) -> list:
        """Fisher-Yates driven by the float stream (used by Mines / Keno)."""
        arr = list(items)
        n = len(arr)
        drawn = self.floats(max(n - 1, 1) + offset)[offset:]
        for i in range(n - 1, 0, -1):
            j = int(drawn[n - 1 - i] * (i + 1))
            j = min(j, i)
            arr[i], arr[j] = arr[j], arr[i]
        return arr

    def pick_indices(self, population: int, k: int, *, offset: int = 0) -> list[int]:
        """k distinct indices from range(population), unbiased."""
        if k > population:
            raise ValueError("cannot pick more items than the population")
        return self.shuffle(list(range(population)))[:k] if offset == 0 else (
            self.shuffle(list(range(population)), offset=offset)[:k]
        )

    def roll_2dp(self) -> float:
        """Dice-style roll in [0.00, 99.99]."""
        return int(self.float() * 10_000) / 100

    def crash_point(self, house_edge: float = 0.01) -> float:
        """Inverse-CDF crash multiplier.

        P(crash >= m) = (1 - edge) / m, so a player cashing out at m has
        EV = (1 - edge) x stake: the house edge is exactly `house_edge`
        regardless of the strategy the player uses.
        """
        u = self.float()
        if u >= 0.999999:
            u = 0.999999
        raw = (1.0 - house_edge) / (1.0 - u)
        crash = int(raw * 100) / 100.0          # floor to 2 decimals
        return min(max(crash, 1.0), _MAX_MULTIPLIER)


def verify(
    *,
    server_seed: str,
    server_seed_hash: str,
    client_seed: str,
    nonce: int,
    house_edge: float = 0.01,
) -> dict:
    """Public verification helper: recompute a crash point from a revealed seed."""
    return {
        "seed_hash_matches": commit(server_seed) == server_seed_hash,
        "crash_point": ProvablyFair(server_seed, client_seed, nonce).crash_point(house_edge),
    }
