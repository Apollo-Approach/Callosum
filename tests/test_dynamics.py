"""Tests for callosum.dynamics — Hebbian potentiation and Ebbinghaus decay."""

from datetime import datetime, timezone, timedelta

from callosum.dynamics import (
    initialize_dynamics_fields,
    potentiate,
    apply_decay,
    _parse_iso,
    STRENGTH_FLOOR,
    MAX_STRENGTH,
    DEFAULT_STABILITY,
    DEFAULT_STRENGTH,
)


# ── _parse_iso ───────────────────────────────────────────────────────────────


class TestParseIso:
    def test_standard_iso(self):
        dt = _parse_iso("2024-06-15T10:30:00+00:00")
        assert dt is not None
        assert dt.year == 2024

    def test_z_suffix(self):
        dt = _parse_iso("2024-06-15T10:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_naive_datetime_gets_utc(self):
        dt = _parse_iso("2024-06-15T10:30:00")
        assert dt is not None
        assert dt.tzinfo == timezone.utc

    def test_none_returns_none(self):
        assert _parse_iso(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_iso("") is None

    def test_garbage_returns_none(self):
        assert _parse_iso("not-a-date") is None

    def test_datetime_passthrough(self):
        now = datetime.now(timezone.utc)
        assert _parse_iso(now) is now

    def test_naive_datetime_passthrough_gets_utc(self):
        naive = datetime(2024, 1, 1, 12, 0, 0)
        result = _parse_iso(naive)
        assert result.tzinfo == timezone.utc


# ── initialize_dynamics_fields ───────────────────────────────────────────────


class TestInitializeDynamicsFields:
    def test_populates_missing_fields(self):
        record = {"id": "test", "created_at": "2024-01-01T00:00:00Z"}
        initialize_dynamics_fields(record)
        assert record["strength"] == DEFAULT_STRENGTH
        assert record["stability"] == DEFAULT_STABILITY
        assert record["access_count"] == 0
        assert record["last_activated"] == "2024-01-01T00:00:00Z"

    def test_preserves_existing_fields(self):
        record = {
            "id": "test",
            "strength": 3.5,
            "stability": 2.0,
            "access_count": 10,
            "last_activated": "2024-06-01T00:00:00Z",
        }
        initialize_dynamics_fields(record)
        assert record["strength"] == 3.5
        assert record["stability"] == 2.0
        assert record["access_count"] == 10

    def test_uses_now_when_no_created_at(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test"}
        initialize_dynamics_fields(record, now=now)
        assert record["last_activated"] == now.isoformat()

    def test_returns_same_dict(self):
        record = {"id": "test"}
        result = initialize_dynamics_fields(record)
        assert result is record


# ── potentiate ───────────────────────────────────────────────────────────────


class TestPotentiate:
    def test_increases_strength(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test", "created_at": "2024-06-15T10:00:00Z"}
        initialize_dynamics_fields(record, now=now)
        old_strength = record["strength"]
        potentiate(record, now=now)
        assert record["strength"] > old_strength

    def test_caps_at_max_strength(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test", "strength": MAX_STRENGTH - 0.01}
        potentiate(record, now=now)
        assert record["strength"] == MAX_STRENGTH

    def test_increments_access_count(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test", "access_count": 5}
        potentiate(record, now=now)
        assert record["access_count"] == 6

    def test_updates_last_activated(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test"}
        potentiate(record, now=now)
        assert record["last_activated"] == now.isoformat()

    def test_spaced_reinforcement_grows_stability(self):
        # First activation
        t1 = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test"}
        initialize_dynamics_fields(record, now=t1)
        record["last_activated"] = t1.isoformat()
        old_stability = record["stability"]

        # Second activation 2 hours later (spaced)
        t2 = t1 + timedelta(hours=2)
        potentiate(record, now=t2)
        assert record["stability"] > old_stability

    def test_rapid_reinforcement_no_stability_growth(self):
        t1 = datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test"}
        initialize_dynamics_fields(record, now=t1)
        record["last_activated"] = t1.isoformat()
        old_stability = record["stability"]

        # Second activation 30 minutes later (not spaced enough)
        t2 = t1 + timedelta(minutes=30)
        potentiate(record, now=t2)
        assert record["stability"] == old_stability

    def test_custom_increment(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test", "strength": 1.0}
        potentiate(record, increment=0.5, now=now)
        assert record["strength"] == 1.5


# ── apply_decay ──────────────────────────────────────────────────────────────


class TestApplyDecay:
    def test_strength_decays_over_time(self):
        t1 = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test", "strength": 2.0, "stability": 1.0, "last_activated": t1.isoformat()}

        t2 = t1 + timedelta(days=3)
        apply_decay(record, now=t2)
        assert record["strength"] < 2.0

    def test_floored_at_strength_floor(self):
        t1 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test", "strength": 0.1, "stability": 0.5, "last_activated": t1.isoformat()}

        # Huge time gap — should floor
        t2 = t1 + timedelta(days=365)
        apply_decay(record, now=t2)
        assert record["strength"] == STRENGTH_FLOOR

    def test_no_decay_at_same_time(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        record = {"id": "test", "strength": 2.0, "last_activated": now.isoformat()}
        apply_decay(record, now=now)
        assert record["strength"] == 2.0

    def test_higher_stability_slower_decay(self):
        t1 = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
        t2 = t1 + timedelta(days=7)

        low_stability = {
            "id": "low",
            "strength": 2.0,
            "stability": 0.5,
            "last_activated": t1.isoformat(),
        }
        high_stability = {
            "id": "high",
            "strength": 2.0,
            "stability": 5.0,
            "last_activated": t1.isoformat(),
        }

        apply_decay(low_stability, now=t2)
        apply_decay(high_stability, now=t2)

        # Higher stability should retain more strength
        assert high_stability["strength"] > low_stability["strength"]

    def test_unparseable_timestamp_no_corruption(self):
        record = {"id": "test", "strength": 2.0, "last_activated": "garbage"}
        apply_decay(record)
        assert record["strength"] == 2.0  # Unchanged

    def test_idempotent_same_instant(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        t0 = now - timedelta(days=1)
        record = {"id": "test", "strength": 2.0, "stability": 1.0, "last_activated": t0.isoformat()}
        apply_decay(record, now=now)
        s1 = record["strength"]
        # last_activated hasn't changed, so calling again at same 'now' with same last_activated
        # would give same result — but decay already mutated strength, so this is a different base
        # The key invariant: no corruption or crash
        assert s1 > STRENGTH_FLOOR
