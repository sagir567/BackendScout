"""Bounded browser and project model usage in SQLite, with no provider calls.

The parent ApplicationStore migration calls ``install_budget_schema(db)`` inside
its transaction before using BrowserBudget. All clients sharing this database
must use the same configured limits and report usage before dispatching work.
Application caps are lifetime caps per (application_id, tracker); monthly caps
span all trackers in Asia/Hebron. Unsettled holds carry into subsequent months
until confirmed usage is settled against their original billing month. There is
deliberately no timeout, cancellation, or automatic reservation release.
External token reservations share only the total project ceiling, never the
browser-specific spending, application, call, action, or active-time caps.
"""

import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

_SCALE = 1_000_000
_MAX_INTEGER = 2**63 - 1
_BILLING_ZONE = ZoneInfo("Asia/Hebron")


class BudgetExceeded(ValueError):
    """A fail-closed refusal with a stable, non-sensitive reason_code.

    Codes: missing_pricing, model_call_limit, action_limit, active_seconds_limit,
    application_limit, monthly_limit, total_monthly_limit.
    """

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class BrowserReservation:
    reservation_id: str
    reserved_micro_nis: int


def install_budget_schema(db: sqlite3.Connection) -> None:
    """Install idempotently without committing the caller's migration transaction."""
    statements = (
        """CREATE TABLE IF NOT EXISTS browser_budget_reservations (
            reservation_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL, tracker TEXT NOT NULL
                CHECK(tracker IN ('test','production')),
            billing_month TEXT NOT NULL, model TEXT NOT NULL,
            input_limit INTEGER NOT NULL CHECK(input_limit >= 0),
            output_limit INTEGER NOT NULL CHECK(output_limit >= 0),
            input_price TEXT NOT NULL, output_price TEXT NOT NULL,
            reserved_micro_nis INTEGER NOT NULL CHECK(reserved_micro_nis >= 0),
            charged_micro_nis INTEGER NOT NULL CHECK(charged_micro_nis >= 0
                AND charged_micro_nis <= reserved_micro_nis),
            state TEXT NOT NULL CHECK(state IN ('reserved','settled')),
            actual_input_tokens INTEGER, actual_output_tokens INTEGER,
            created_at TEXT NOT NULL,
            CHECK((state = 'reserved' AND actual_input_tokens IS NULL
                AND actual_output_tokens IS NULL) OR
                (state = 'settled' AND actual_input_tokens IS NOT NULL
                AND actual_output_tokens IS NOT NULL
                AND actual_input_tokens BETWEEN 0 AND input_limit
                AND actual_output_tokens BETWEEN 0 AND output_limit)))""",
        """CREATE INDEX IF NOT EXISTS browser_budget_application
            ON browser_budget_reservations(application_id, tracker)""",
        """CREATE INDEX IF NOT EXISTS browser_budget_month
            ON browser_budget_reservations(billing_month)""",
        """CREATE INDEX IF NOT EXISTS browser_budget_pending
            ON browser_budget_reservations(state) WHERE state = 'reserved'""",
        """CREATE TABLE IF NOT EXISTS browser_budget_actions (
            application_id TEXT NOT NULL, tracker TEXT NOT NULL
                CHECK(tracker IN ('test','production')),
            actions INTEGER NOT NULL CHECK(actions >= 0),
            active_microseconds INTEGER NOT NULL CHECK(active_microseconds >= 0),
            PRIMARY KEY(application_id, tracker))""",
        """CREATE TABLE IF NOT EXISTS browser_budget_external_usage (
            usage_id TEXT PRIMARY KEY, billing_month TEXT NOT NULL,
            cost_micro_nis INTEGER NOT NULL CHECK(cost_micro_nis >= 0))""",
        """CREATE INDEX IF NOT EXISTS browser_budget_external_month
            ON browser_budget_external_usage(billing_month)""",
        """CREATE TABLE IF NOT EXISTS project_budget_reservations (
            reservation_id TEXT PRIMARY KEY,
            billing_month TEXT NOT NULL, model TEXT NOT NULL,
            input_limit INTEGER NOT NULL CHECK(input_limit >= 0),
            output_limit INTEGER NOT NULL CHECK(output_limit >= 0),
            input_price TEXT NOT NULL, output_price TEXT NOT NULL,
            reserved_micro_nis INTEGER NOT NULL CHECK(reserved_micro_nis >= 0),
            charged_micro_nis INTEGER NOT NULL CHECK(charged_micro_nis >= 0
                AND charged_micro_nis <= reserved_micro_nis),
            state TEXT NOT NULL CHECK(state IN ('reserved','settled')),
            actual_input_tokens INTEGER, actual_output_tokens INTEGER,
            created_at TEXT NOT NULL,
            CHECK((state = 'reserved' AND actual_input_tokens IS NULL
                AND actual_output_tokens IS NULL) OR
                (state = 'settled' AND actual_input_tokens IS NOT NULL
                AND actual_output_tokens IS NOT NULL
                AND actual_input_tokens BETWEEN 0 AND input_limit
                AND actual_output_tokens BETWEEN 0 AND output_limit)))""",
        """CREATE INDEX IF NOT EXISTS project_budget_month
            ON project_budget_reservations(billing_month)""",
        """CREATE INDEX IF NOT EXISTS project_budget_pending
            ON project_budget_reservations(state) WHERE state = 'reserved'""",
    )
    for statement in statements:
        db.execute(statement)


def _now() -> datetime:
    return datetime.now(UTC)


def _integer(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_INTEGER:
        raise ValueError(f"{name} must be a non-negative SQLite integer")
    return value


def _decimal(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise TypeError("Amounts and prices must be decimal strings, Decimal, or integers")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid decimal amount or price") from None
    if not number.is_finite() or number < 0 or number > _MAX_INTEGER:
        raise ValueError("Amounts and prices must be finite and non-negative")
    return number


def _money(value) -> int:
    scaled = Fraction(_decimal(value)) * _SCALE
    if scaled.denominator != 1:
        raise ValueError("Money limits and charges require at most six decimal places")
    return _integer(scaled.numerator, "Micro-NIS amount")


def _microseconds(value, *, round_up: bool) -> int:
    # Elapsed time may come from a monotonic clock; money never accepts floats.
    scaled = Fraction(_decimal(str(value) if isinstance(value, float) else value)) * _SCALE
    whole, remainder = divmod(scaled.numerator, scaled.denominator)
    return _integer(whole + int(round_up and remainder != 0), "Microseconds")


def _cost(input_tokens: int, output_tokens: int, input_price: str, output_price: str) -> int:
    # NIS/million tokens times tokens equals micro-NIS. Fraction avoids Decimal
    # context rounding; round the combined charge up once, never down.
    exact = input_tokens * Fraction(input_price) + output_tokens * Fraction(output_price)
    whole, remainder = divmod(exact.numerator, exact.denominator)
    return _integer(whole + int(remainder != 0), "Token cost")


def _identity(application_id, tracker) -> tuple[str, str]:
    if not isinstance(application_id, str) or not application_id.strip():
        raise ValueError("Application identity is required")
    if tracker not in ("test", "production"):
        raise ValueError("Tracker must be test or production")
    return application_id, "test" if tracker == "test" else "production"


def _check_money(used: int, requested: int, limit: int, reason: str) -> None:
    if used >= limit or used + requested > limit:
        raise BudgetExceeded(reason)


class BrowserBudget:
    """Use an already migrated workflow database; snapshot money is micro-NIS.

    Price entries require input_nis_per_million and output_nis_per_million.
    A missing entry never falls back to another model or a zero price. This
    ledger meters caller-reported work; it does not run a browser or a timer.
    """

    def __init__(
        self,
        database_path,
        *,
        monthly_limit_nis="40",
        application_limit_nis="2",
        total_monthly_limit_nis="100",
        fixed_monthly_nis="0",
        max_model_calls=6,
        max_actions=40,
        max_active_seconds=600,
        prices=None,
    ):
        self.database_path = Path(database_path).expanduser().resolve()
        self.monthly_limit_micro_nis = _money(monthly_limit_nis)
        self.application_limit_micro_nis = _money(application_limit_nis)
        self.total_monthly_limit_micro_nis = _money(total_monthly_limit_nis)
        self.fixed_monthly_micro_nis = _money(fixed_monthly_nis)
        self.max_model_calls = _integer(max_model_calls, "Model call cap")
        self.max_actions = _integer(max_actions, "Action cap")
        self.max_active_microseconds = _microseconds(max_active_seconds, round_up=False)
        if prices is not None and not isinstance(prices, Mapping):
            raise ValueError("Prices must be a model mapping")
        self._prices = {}
        for model, rates in (prices or {}).items():
            if not isinstance(model, str) or not model.strip() or not isinstance(rates, Mapping):
                raise ValueError("Prices must map model names to rate mappings")
            self._prices[model] = {
                key: str(_decimal(rates[key]))
                for key in ("input_nis_per_million", "output_nis_per_million")
                if key in rates
            }

    @contextmanager
    def _transaction(self, *, write=True):
        db = sqlite3.connect(
            self.database_path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None
        )
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _snapshot(self, db, application_id: str, tracker: str, month: str) -> dict:
        application = db.execute(
            "SELECT charged_micro_nis,state FROM browser_budget_reservations "
            "WHERE application_id=? AND tracker=?",
            (application_id, tracker),
        ).fetchall()
        browser_cost = sum(
            row[0]
            for row in db.execute(
                "SELECT charged_micro_nis FROM browser_budget_reservations "
                "WHERE billing_month=? OR state='reserved'",
                (month,),
            )
        )
        external_cost = sum(
            row[0]
            for row in db.execute(
                "SELECT cost_micro_nis FROM browser_budget_external_usage WHERE billing_month=?",
                (month,),
            )
        )
        external = db.execute(
            "SELECT charged_micro_nis,state,input_limit,output_limit,"
            "actual_input_tokens,actual_output_tokens FROM project_budget_reservations "
            "WHERE billing_month=? OR state='reserved'",
            (month,),
        ).fetchall()
        external_pending = [row for row in external if row["state"] == "reserved"]
        external_token_cost = sum(row["charged_micro_nis"] for row in external)
        external_cost += external_token_cost
        actions = db.execute(
            "SELECT actions,active_microseconds FROM browser_budget_actions "
            "WHERE application_id=? AND tracker=?",
            (application_id, tracker),
        ).fetchone()
        count, microseconds = tuple(actions) if actions is not None else (0, 0)
        seconds, fraction = divmod(microseconds, _SCALE)
        return {
            "billing_month": month,
            "model_calls": len(application),
            "pending_reservations": sum(row["state"] == "reserved" for row in application),
            "actions": count,
            "active_microseconds": microseconds,
            "active_seconds": f"{seconds}.{fraction:06d}",
            "application_micro_nis": sum(row["charged_micro_nis"] for row in application),
            "monthly_browser_micro_nis": browser_cost,
            "monthly_external_micro_nis": external_cost,
            "monthly_external_token_micro_nis": external_token_cost,
            "monthly_external_model_calls": len(external),
            "monthly_external_pending_reservations": len(external_pending),
            "monthly_external_input_tokens": sum(
                row["actual_input_tokens"] or 0 for row in external
            ),
            "monthly_external_output_tokens": sum(
                row["actual_output_tokens"] or 0 for row in external
            ),
            "monthly_external_reserved_input_tokens": sum(
                row["input_limit"] for row in external_pending
            ),
            "monthly_external_reserved_output_tokens": sum(
                row["output_limit"] for row in external_pending
            ),
            "fixed_monthly_micro_nis": self.fixed_monthly_micro_nis,
            "monthly_total_micro_nis": browser_cost + external_cost + self.fixed_monthly_micro_nis,
        }

    def _quote(self, model, input_tokens, max_output_tokens) -> tuple[str, str, int]:
        _integer(input_tokens, "Input tokens")
        _integer(max_output_tokens, "Output token cap")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Model identity is required")
        rates = self._prices.get(model, {})
        try:
            input_price = rates["input_nis_per_million"]
            output_price = rates["output_nis_per_million"]
        except KeyError:
            raise BudgetExceeded("missing_pricing") from None
        cost = _cost(input_tokens, max_output_tokens, input_price, output_price)
        return input_price, output_price, cost

    def reserve(
        self, application_id, tracker, model, input_tokens, max_output_tokens
    ) -> BrowserReservation:
        """Commit a worst-case hold before the model call; a retry is a new call."""
        application_id, tracker = _identity(application_id, tracker)
        input_price, output_price, cost = self._quote(model, input_tokens, max_output_tokens)
        with self._transaction() as db:
            now = _now()
            month = now.astimezone(_BILLING_ZONE).strftime("%Y-%m")
            usage = self._snapshot(db, application_id, tracker, month)
            if usage["model_calls"] >= self.max_model_calls:
                raise BudgetExceeded("model_call_limit")
            self._check_activity(usage)
            _check_money(
                usage["application_micro_nis"],
                cost,
                self.application_limit_micro_nis,
                "application_limit",
            )
            _check_money(
                usage["monthly_browser_micro_nis"],
                cost,
                self.monthly_limit_micro_nis,
                "monthly_limit",
            )
            _check_money(
                usage["monthly_total_micro_nis"],
                cost,
                self.total_monthly_limit_micro_nis,
                "total_monthly_limit",
            )
            reservation = BrowserReservation(uuid4().hex, cost)
            db.execute(
                """INSERT INTO browser_budget_reservations (
                    reservation_id,application_id,tracker,billing_month,model,
                    input_limit,output_limit,input_price,output_price,
                    reserved_micro_nis,charged_micro_nis,state,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,'reserved',?)""",
                (
                    reservation.reservation_id,
                    application_id,
                    tracker,
                    month,
                    model,
                    input_tokens,
                    max_output_tokens,
                    input_price,
                    output_price,
                    cost,
                    cost,
                    now.isoformat(),
                ),
            )
            return reservation

    def reserve_external(self, model, input_tokens, max_output_tokens) -> BrowserReservation:
        """Hold a non-browser call's worst-case cost under only the shared total cap.

        This does not consume browser call, application, action, or time budgets.
        Missing pricing fails closed, and every retry needs its own reservation.
        Do not also record_external_usage for this call: that would double count.
        """
        input_price, output_price, cost = self._quote(model, input_tokens, max_output_tokens)
        with self._transaction() as db:
            now = _now()
            month = now.astimezone(_BILLING_ZONE).strftime("%Y-%m")
            usage = self._snapshot(db, "", "test", month)
            _check_money(
                usage["monthly_total_micro_nis"],
                cost,
                self.total_monthly_limit_micro_nis,
                "total_monthly_limit",
            )
            reservation = BrowserReservation(uuid4().hex, cost)
            db.execute(
                """INSERT INTO project_budget_reservations (
                    reservation_id,billing_month,model,input_limit,output_limit,
                    input_price,output_price,reserved_micro_nis,charged_micro_nis,state,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,'reserved',?)""",
                (
                    reservation.reservation_id,
                    month,
                    model,
                    input_tokens,
                    max_output_tokens,
                    input_price,
                    output_price,
                    cost,
                    cost,
                    now.isoformat(),
                ),
            )
            return reservation

    def settle(self, reservation_id, input_tokens, output_tokens) -> int | None:
        """Return actual micro-NIS; None tokens mean unknown, keeping the full hold.

        Confirmed usage must not exceed either reserved token bound. Identical
        settlements are idempotent; conflicting settlements cannot rewrite costs.
        Stored prices, not current configuration, determine the actual charge.
        """
        return self._settle(reservation_id, input_tokens, output_tokens, external=False)

    def settle_external(self, reservation_id, input_tokens, output_tokens) -> int | None:
        """Settle a project token hold, with the same unknown/idempotent rules as settle.

        Return actual micro-NIS, or None for unknown usage, retaining the full
        hold. This method cannot settle a browser reservation or change activity.
        """
        return self._settle(reservation_id, input_tokens, output_tokens, external=True)

    def _settle(self, reservation_id, input_tokens, output_tokens, *, external) -> int | None:
        table = "project_budget_reservations" if external else "browser_budget_reservations"
        for value in (input_tokens, output_tokens):
            if value is not None:
                _integer(value, "Actual tokens")
        with self._transaction() as db:
            row = db.execute(
                f"SELECT * FROM {table} WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Reservation was not found")
            if (input_tokens is not None and input_tokens > row["input_limit"]) or (
                output_tokens is not None and output_tokens > row["output_limit"]
            ):
                raise ValueError("Actual tokens exceed reserved limits")
            if input_tokens is None or output_tokens is None:
                return None
            if row["state"] == "settled":
                if (input_tokens, output_tokens) != (
                    row["actual_input_tokens"],
                    row["actual_output_tokens"],
                ):
                    raise ValueError("Conflicting settlement cannot overwrite confirmed usage")
                return row["charged_micro_nis"]
            cost = _cost(input_tokens, output_tokens, row["input_price"], row["output_price"])
            db.execute(
                f"UPDATE {table} SET state='settled', "
                "charged_micro_nis=?,actual_input_tokens=?,actual_output_tokens=? "
                "WHERE reservation_id=?",
                (cost, input_tokens, output_tokens, reservation_id),
            )
            return cost

    def _check_activity(self, usage) -> None:
        if usage["actions"] >= self.max_actions:
            raise BudgetExceeded("action_limit")
        if usage["active_microseconds"] >= self.max_active_microseconds:
            raise BudgetExceeded("active_seconds_limit")

    def record_action(self, application_id, tracker, elapsed_seconds=0) -> None:
        """Book one action and incremental active time before allowing more work.

        This is not a session-total replacement. Rejected increments are atomic;
        accepted increments survive retries, worker crashes, and month boundaries.
        """
        self._record_activity(application_id, tracker, elapsed_seconds, increment_action=True)

    def record_active(self, application_id, tracker, elapsed_seconds) -> None:
        """Book incremental active time without consuming a browser action.

        Callers report model wait and end-of-attempt time, excluding human wait.
        Action and model-call exhaustion do not prevent this accounting. The
        same active-time cap applies; rejected increments leave counters intact.
        """
        self._record_activity(application_id, tracker, elapsed_seconds, increment_action=False)

    def _record_activity(self, application_id, tracker, elapsed_seconds, *, increment_action):
        application_id, tracker = _identity(application_id, tracker)
        elapsed = _microseconds(elapsed_seconds, round_up=True)
        with self._transaction() as db:
            row = db.execute(
                "SELECT actions,active_microseconds FROM browser_budget_actions "
                "WHERE application_id=? AND tracker=?",
                (application_id, tracker),
            ).fetchone()
            count, active = tuple(row) if row is not None else (0, 0)
            if increment_action and count >= self.max_actions:
                raise BudgetExceeded("action_limit")
            if (
                active >= self.max_active_microseconds
                or active + elapsed > self.max_active_microseconds
            ):
                raise BudgetExceeded("active_seconds_limit")
            db.execute(
                "INSERT INTO browser_budget_actions VALUES (?,?,?,?) "
                "ON CONFLICT(application_id,tracker) DO UPDATE SET "
                "actions=excluded.actions,active_microseconds=excluded.active_microseconds",
                (application_id, tracker, count + int(increment_action), active + elapsed),
            )

    def record_external_usage(self, usage_id: str, cost_nis) -> None:
        """Book non-browser model cost before dispatch under the shared total cap.

        usage_id must be a stable opaque event key, never a secret. Replay is
        idempotent even after a month rollover; changing an event's cost fails.
        """
        if not isinstance(usage_id, str) or not usage_id.strip():
            raise ValueError("External usage identity is required")
        cost = _money(cost_nis)
        with self._transaction() as db:
            previous = db.execute(
                "SELECT cost_micro_nis FROM browser_budget_external_usage WHERE usage_id=?",
                (usage_id,),
            ).fetchone()
            if previous is not None:
                if previous[0] != cost:
                    raise ValueError("Conflicting external usage cannot overwrite recorded cost")
                return
            month = _now().astimezone(_BILLING_ZONE).strftime("%Y-%m")
            usage = self._snapshot(db, "", "test", month)
            _check_money(
                usage["monthly_total_micro_nis"],
                cost,
                self.total_monthly_limit_micro_nis,
                "total_monthly_limit",
            )
            db.execute(
                "INSERT INTO browser_budget_external_usage VALUES (?,?,?)", (usage_id, month, cost)
            )

    def snapshot(self, application_id, tracker) -> dict:
        """Return JSON-safe aggregates without identities or secrets; money is micro-NIS.

        monthly_external_micro_nis combines explicit cost events and token holds
        or settled charges. monthly_external_token_micro_nis isolates token cost.
        External token counts separate confirmed usage from outstanding bounds;
        monthly aggregates include unresolved holds from prior months as well.
        """
        application_id, tracker = _identity(application_id, tracker)
        with self._transaction(write=False) as db:
            month = _now().astimezone(_BILLING_ZONE).strftime("%Y-%m")
            return self._snapshot(db, application_id, tracker, month)
