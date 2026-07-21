"""Local receipt history (WS-B items 5 + E5): stdlib sqlite3, hash-chained.

Every stored receipt embeds the previous receipt's hash (hashlib SHA-256 over the
canonical receipt JSON), so a season of receipts is a tamper-evident chain: change
any past receipt and verify_chain() fails. pattern_summary() reads the chain to
surface the cross-period story ("shorted X of last Y periods, $Z cumulative") with
per-period statute citations.

Nothing leaves the device — this is the whole persistence story (no server, ever).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal

from core.contracts import Receipt

GENESIS = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipts (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    shortfall    TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    chain_hash   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
"""


def _canonical(receipt: Receipt) -> str:
    """Deterministic bytes to hash: the receipt WITHOUT its own chain_hash."""
    d = receipt.to_dict()
    d.pop("chain_hash", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _hash(prev_hash: str, receipt: Receipt) -> str:
    return hashlib.sha256((prev_hash + _canonical(receipt)).encode("utf-8")).hexdigest()


class ReceiptHistory:
    def __init__(self, db_path: str = ":memory:", check_same_thread: bool = True) -> None:
        # check_same_thread=False lets the FastAPI app share one connection across its
        # request threadpool. The lock is CORRECTNESS, not throughput (WS-F security
        # finding 1): store() is a read-modify-write on the chain head, and an unlocked
        # interleave forks the chain — verify_chain() then fails with no tampering.
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        # WS-F security finding 2: don't ride the compile-time default — deleted wage
        # history must not linger in freelist pages on any build.
        self.conn.execute("PRAGMA secure_delete=ON")
        self.conn.execute(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT chain_hash FROM receipts ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS

    def store(self, receipt: Receipt) -> str:
        """Persist a receipt, chaining it to the previous. Sets receipt.chain_hash. Returns the hash.
        Thread-safe: the whole read-head→hash→insert→commit sequence is one critical section."""
        with self._lock:
            return self._store_locked(receipt)

    def _store_locked(self, receipt: Receipt) -> str:
        prev = self._last_hash()
        chain_hash = _hash(prev, receipt)
        receipt.chain_hash = chain_hash
        d = receipt.to_dict()
        self.conn.execute(
            "INSERT INTO receipts (session_id, period_start, period_end, shortfall, "
            "receipt_json, prev_hash, chain_hash, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                receipt.session_id,
                # kn: Receipt carries no period bounds; join on session_id. period_* kept
                # nullable for when a caller wants to denormalize the range later.
                "",
                "",
                receipt.shortfall,
                json.dumps(d, sort_keys=True, separators=(",", ":")),
                prev,
                chain_hash,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()
        return chain_hash

    def wipe(self) -> int:
        """Erase all receipts, for real: secure_delete overwrites the row bytes and
        VACUUM rebuilds the file so nothing lingers in freelist pages. Returns count."""
        with self._lock:
            n = self.conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
            self.conn.execute("DELETE FROM receipts")
            self.conn.commit()
            self.conn.execute("VACUUM")
            return n

    def verify_chain(self) -> bool:
        """Recompute the whole chain from stored bytes; any mismatch → False (tampered)."""
        prev = GENESIS
        for row in self.conn.execute(
            "SELECT receipt_json, prev_hash, chain_hash FROM receipts ORDER BY seq"
        ):
            receipt_json, stored_prev, stored_hash = row
            if stored_prev != prev:
                return False
            recomputed = hashlib.sha256(
                (prev + _strip_chain_hash(receipt_json)).encode("utf-8")
            ).hexdigest()
            if recomputed != stored_hash:
                return False
            prev = stored_hash
        return True

    def pattern_summary(self, last_n: int | None = None) -> dict:
        """'shorted X of last Y periods, $Z cumulative', with per-period citations."""
        q = "SELECT session_id, shortfall, receipt_json FROM receipts ORDER BY seq DESC"
        if last_n:
            q += f" LIMIT {int(last_n)}"
        rows = self.conn.execute(q).fetchall()
        rows = list(reversed(rows))  # chronological

        shorted: list[dict] = []
        cumulative = Decimal(0)
        for session_id, shortfall, receipt_json in rows:
            sf = Decimal(shortfall)
            if sf > 0:
                cumulative += sf
                receipt = json.loads(receipt_json)
                citations = sorted({ln["citation"] for ln in receipt.get("lines", [])})
                shorted.append(
                    {"session_id": session_id, "shortfall": str(sf), "citations": citations}
                )

        y = len(rows)
        x = len(shorted)
        return {
            "summary": f"shorted {x} of last {y} periods, ${cumulative} cumulative",
            "shorted_periods": x,
            "total_periods": y,
            "cumulative_shortfall": str(cumulative),
            "periods": shorted,
        }


def _strip_chain_hash(receipt_json: str) -> str:
    d = json.loads(receipt_json)
    d.pop("chain_hash", None)
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def demo() -> None:
    from core.contracts import ReceiptLine

    def _rc(sid, shortfall) -> Receipt:
        return Receipt(
            session_id=sid,
            lines=[ReceiptLine("rest_pay", "20.00", "LC §226.2(a)(3)", "0.5h × $40", [], {})],
            total_owed="320.00",
            amount_paid=str(Decimal("320.00") - Decimal(shortfall)),
            shortfall=shortfall,
            claim_value={"liquidated_damages": shortfall, "interest": "0.00", "citation": "LC §1194.2"},
            lower_bound=False,
            missing_days=[],
            disclaimer="es+en",
        )

    h = ReceiptHistory(":memory:")
    h.store(_rc("w1", "20.00"))
    h.store(_rc("w2", "0.00"))
    h.store(_rc("w3", "15.00"))
    assert h.verify_chain() is True, "fresh chain must verify"

    ps = h.pattern_summary()
    assert ps["shorted_periods"] == 2 and ps["total_periods"] == 3, ps
    assert ps["cumulative_shortfall"] == "35.00", ps
    assert "LC §226.2(a)(3)" in ps["periods"][0]["citations"], ps

    # tamper: rewrite a stored receipt's shortfall in place → chain must break
    h.conn.execute("UPDATE receipts SET receipt_json = REPLACE(receipt_json, '20.00', '99.00') WHERE seq = 1")
    h.conn.commit()
    assert h.verify_chain() is False, "tampered chain must fail verification"

    print("history.py: hash-chain + pattern_summary self-checks passed")
    print("  pattern:", ps["summary"])


if __name__ == "__main__":
    demo()
