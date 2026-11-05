import json
import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from threading import Barrier

import pytest

from backend_scout import browser_budget
from backend_scout.browser_budget import (
    BrowserBudget,
    BudgetExceeded,
    install_budget_schema,
)

PRICES = {"fixture-model": {"input_nis_per_million": "1", "output_nis_per_million": "2"}}


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "workflow.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        install_budget_schema(db)
    return path


@pytest.fixture
def budget(database):
    return BrowserBudget(database, prices=PRICES)


@pytest.fixture
def clock(monkeypatch):
    times = [datetime(2026, 1, 15, 10, tzinfo=UTC)]
    monkeypatch.setattr(browser_budget, "_now", lambda: times[0])
    return times


def reserve_one(budget, application_id="job", tracker="test"):
    return budget.reserve(application_id, tracker, "fixture-model", 1_000_000, 0)


def assert_refused(reason, operation):
    with pytest.raises(BudgetExceeded) as error:
        operation()
    assert error.value.reason_code == reason
    assert str(error.value) == reason


def test_schema_is_idempotent_and_part_of_parent_migration(tmp_path):
    path = tmp_path / "parent.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE parent_events (id INTEGER)")
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO parent_events VALUES (1)")
        install_budget_schema(db)
        install_budget_schema(db)
        assert db.in_transaction
        db.rollback()
        assert db.execute("SELECT * FROM parent_events").fetchall() == []
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'browser_budget_%' "
                "OR name LIKE 'project_budget_%'"
            ).fetchall()
            == []
        )
        db.execute("BEGIN IMMEDIATE")
        install_budget_schema(db)
    BrowserBudget(path, prices=PRICES).reserve("job", "test", "fixture-model", 0, 0)


def test_budget_requires_existing_parent_database(tmp_path):
    path = tmp_path / "not-migrated.sqlite3"
    budget = BrowserBudget(path, prices=PRICES)
    with pytest.raises(sqlite3.OperationalError):
        reserve_one(budget)
    assert not path.exists()


def test_worst_case_hold_and_settlement_use_integer_micro_nis(budget):
    reservation = budget.reserve("job", "test", "fixture-model", 200_000, 400_000)
    assert reservation.reservation_id
    assert reservation.reserved_micro_nis == 1_000_000
    held = budget.snapshot("job", "test")
    assert held["model_calls"] == held["pending_reservations"] == 1
    assert held["application_micro_nis"] == held["monthly_browser_micro_nis"] == 1_000_000
    assert budget.settle(reservation.reservation_id, 100_000, 100_000) == 300_000
    settled = budget.snapshot("job", "test")
    assert settled["pending_reservations"] == 0
    assert settled["model_calls"] == 1
    assert settled["application_micro_nis"] == settled["monthly_total_micro_nis"] == 300_000
    assert budget.settle(reservation.reservation_id, 100_000, 100_000) == 300_000
    with pytest.raises(ValueError, match="Conflicting settlement"):
        budget.settle(reservation.reservation_id, 0, 0)
    assert budget.snapshot("job", "test") == settled


def test_reservation_prices_survive_restart_and_config_changes(database):
    original = BrowserBudget(database, prices=PRICES)
    reservation = original.reserve("job", "test", "fixture-model", 200_000, 400_000)
    repriced = BrowserBudget(
        database,
        prices={"fixture-model": {"input_nis_per_million": "100", "output_nis_per_million": "200"}},
    )
    assert repriced.settle(reservation.reservation_id, 100_000, 100_000) == 300_000
    assert BrowserBudget(database).settle(reservation.reservation_id, 100_000, 100_000) == 300_000


def test_worst_case_output_refused_before_dispatch_and_confirmed_savings_are_reusable(database):
    budget = BrowserBudget(database, application_limit_nis="1", prices=PRICES)
    assert_refused(
        "application_limit", lambda: budget.reserve("job", "test", "fixture-model", 1, 500_000)
    )
    assert budget.snapshot("job", "test")["model_calls"] == 0
    held = reserve_one(budget)
    assert_refused("application_limit", lambda: reserve_one(budget))
    assert budget.settle(held.reservation_id, 1, 0) == 1
    budget.reserve("job", "test", "fixture-model", 999_999, 0)
    assert budget.snapshot("job", "test")["application_micro_nis"] == 1_000_000
    with sqlite3.connect(database) as db:
        assert db.execute(
            "SELECT DISTINCT typeof(charged_micro_nis),typeof(input_price) "
            "FROM browser_budget_reservations"
        ).fetchall() == [("integer", "text")]


@pytest.mark.parametrize(
    "prices",
    [
        None,
        {},
        {"different-model": PRICES["fixture-model"]},
        {"fixture-model": {}},
        {"fixture-model": {"input_nis_per_million": "1"}},
        {"fixture-model": {"output_nis_per_million": "1"}},
    ],
)
def test_missing_price_is_fail_closed_without_charging(database, prices):
    budget = BrowserBudget(database, prices=prices)
    assert_refused("missing_pricing", lambda: reserve_one(budget))
    assert_refused("missing_pricing", lambda: budget.reserve_external("fixture-model", 1, 1))
    assert budget.snapshot("job", "test")["model_calls"] == 0
    assert budget.snapshot("job", "test")["monthly_external_model_calls"] == 0


def test_explicit_zero_prices_are_not_missing(database):
    budget = BrowserBudget(
        database, prices={"free": {"input_nis_per_million": "0", "output_nis_per_million": "0"}}
    )
    result = budget.reserve("job", "test", "free", 100, 100)
    assert result.reserved_micro_nis == 0
    assert budget.snapshot("job", "test")["model_calls"] == 1


def test_fractional_prices_round_combined_charge_up_without_decimal_context_loss(database):
    budget = BrowserBudget(
        database,
        prices={
            "fractional": {"input_nis_per_million": "0.4", "output_nis_per_million": "0.4"},
            "precise": {
                "input_nis_per_million": "0.99999999999999999999999999999999999",
                "output_nis_per_million": "0.00000000000000000000000000000000002",
            },
        },
    )
    with localcontext() as context:
        context.prec = 3
        assert budget.reserve("one", "test", "fractional", 1, 1).reserved_micro_nis == 1
        assert budget.reserve("two", "test", "precise", 1, 1).reserved_micro_nis == 2
        assert budget.snapshot("two", "test")["monthly_browser_micro_nis"] == 3


def test_application_cost_cap_cumulative_across_calls_and_restart(database):
    first = BrowserBudget(database, prices=PRICES)
    reserve_one(first)
    restarted = BrowserBudget(database, prices=PRICES)
    reserve_one(restarted)
    before = restarted.snapshot("job", "test")
    assert_refused("application_limit", lambda: reserve_one(restarted))
    assert restarted.snapshot("job", "test") == before
    # Tracker identities have separate lifetime caps, but share monthly spend.
    reserve_one(restarted, tracker="production")
    assert restarted.snapshot("job", "production")["application_micro_nis"] == 1_000_000
    assert restarted.snapshot("job", "test")["monthly_browser_micro_nis"] == 3_000_000


def test_browser_monthly_cap_spans_applications_and_trackers(database):
    budget = BrowserBudget(database, monthly_limit_nis="1", prices=PRICES)
    reserve_one(budget, "first")
    assert_refused("monthly_limit", lambda: reserve_one(budget, "second", "production"))
    assert budget.snapshot("second", "production")["model_calls"] == 0


def test_shared_monthly_cap_includes_fixed_and_external_costs(database):
    budget = BrowserBudget(database, fixed_monthly_nis="98", prices=PRICES)
    budget.record_external_usage("event-1", "1")
    reserve_one(budget)
    assert_refused("total_monthly_limit", lambda: reserve_one(budget, "other"))
    assert_refused("total_monthly_limit", lambda: budget.record_external_usage("event-2", "0.1"))
    usage = budget.snapshot("job", "test")
    assert usage["monthly_external_micro_nis"] == 1_000_000
    assert usage["fixed_monthly_micro_nis"] == 98_000_000
    assert usage["monthly_total_micro_nis"] == 100_000_000


def test_external_usage_is_idempotent_and_cannot_be_reduced(database, clock):
    budget = BrowserBudget(database, prices=PRICES)
    budget.record_external_usage("stable-key", Decimal("20.5"))
    restarted = BrowserBudget(database, prices=PRICES)
    restarted.record_external_usage("stable-key", "20.500000")
    with pytest.raises(ValueError, match="Conflicting external"):
        restarted.record_external_usage("stable-key", "0")
    assert restarted.snapshot("job", "test")["monthly_external_micro_nis"] == 20_500_000
    clock[0] = datetime(2026, 2, 15, tzinfo=UTC)
    restarted.record_external_usage("stable-key", "20.5")
    assert restarted.snapshot("job", "test")["monthly_external_micro_nis"] == 0


@pytest.mark.parametrize("fixed", ["100", "101"])
def test_exhausted_fixed_cost_alone_refuses_work(database, fixed):
    budget = BrowserBudget(database, fixed_monthly_nis=fixed, prices=PRICES)
    assert_refused("total_monthly_limit", lambda: reserve_one(budget))


def test_model_calls_are_cumulative_even_for_zero_cost_settlements(database):
    for _ in range(6):
        budget = BrowserBudget(database, prices=PRICES)
        reservation = budget.reserve("job", "test", "fixture-model", 0, 0)
        assert budget.settle(reservation.reservation_id, 0, 0) == 0
    assert_refused("model_call_limit", lambda: reserve_one(budget))
    assert budget.snapshot("job", "test")["model_calls"] == 6


def test_actions_persist_across_retries_and_block_further_model_work(database):
    for _ in range(40):
        BrowserBudget(database, prices=PRICES).record_action("job", "test")
    budget = BrowserBudget(database, prices=PRICES)
    assert_refused("action_limit", lambda: budget.record_action("job", "test"))
    assert_refused("action_limit", lambda: reserve_one(budget))
    assert budget.snapshot("job", "test")["actions"] == 40
    budget.record_action("job", "production")
    assert budget.snapshot("job", "production")["actions"] == 1


def test_active_time_is_incremental_and_rejected_increment_is_atomic(database):
    budget = BrowserBudget(database, prices=PRICES)
    budget.record_action("job", "test", "200.25")
    restarted = BrowserBudget(database, prices=PRICES)
    assert_refused("active_seconds_limit", lambda: restarted.record_action("job", "test", 400))
    assert restarted.snapshot("job", "test")["actions"] == 1
    restarted.record_action("job", "test", Decimal("399.75"))
    before = restarted.snapshot("job", "test")
    assert before["active_microseconds"] == 600_000_000
    assert before["active_seconds"] == "600.000000"
    assert_refused("active_seconds_limit", lambda: restarted.record_action("job", "test"))
    assert_refused("active_seconds_limit", lambda: reserve_one(restarted))
    assert restarted.snapshot("job", "test") == before


def test_record_active_persists_without_actions_and_shares_action_time_cap(database):
    budget = BrowserBudget(database, prices=PRICES)
    budget.record_active("job", "test", "200.25")
    initial = budget.snapshot("job", "test")
    assert initial["actions"] == initial["model_calls"] == 0
    assert initial["active_microseconds"] == 200_250_000
    restarted = BrowserBudget(database, prices=PRICES)
    restarted.record_action("job", "test", 100)
    restarted.record_active("job", "test", Decimal("299.75"))
    before = restarted.snapshot("job", "test")
    assert before["actions"] == 1
    assert before["active_seconds"] == "600.000000"
    assert_refused("active_seconds_limit", lambda: restarted.record_active("job", "test", 0))
    assert_refused("active_seconds_limit", lambda: restarted.record_action("job", "test"))
    assert_refused("active_seconds_limit", lambda: reserve_one(restarted))
    assert restarted.snapshot("job", "test") == before
    restarted.record_active("job", "production", 1)
    assert restarted.snapshot("job", "production")["active_microseconds"] == 1_000_000


def test_record_active_can_account_after_action_and_model_call_exhaustion(database):
    budget = BrowserBudget(database, max_actions=1, max_model_calls=1, prices=PRICES)
    reservation = reserve_one(budget)
    budget.record_action("job", "test", 10)
    budget.record_active("job", "test", 20)
    assert budget.snapshot("job", "test")["actions"] == 1
    assert budget.snapshot("job", "test")["active_seconds"] == "30.000000"
    assert_refused("action_limit", lambda: budget.record_action("job", "test"))
    assert_refused("model_call_limit", lambda: reserve_one(budget))
    assert budget.settle(reservation.reservation_id, 0, 0) == 0


def test_record_active_does_not_require_model_or_action_allowance(database):
    budget = BrowserBudget(database, max_actions=0, max_model_calls=0, monthly_limit_nis="0")
    budget.record_active("job", "test", 1)
    usage = budget.snapshot("job", "test")
    assert usage["actions"] == usage["model_calls"] == usage["application_micro_nis"] == 0
    assert usage["active_microseconds"] == 1_000_000


@pytest.mark.parametrize(("initial", "elapsed", "limit"), [(0, 601, 600), (1, 600, 600), (0, 0, 0)])
def test_rejected_record_active_is_atomic(database, initial, elapsed, limit):
    budget = BrowserBudget(database, max_active_seconds=limit)
    if initial:
        budget.record_active("job", "test", initial)
    before = budget.snapshot("job", "test")
    assert_refused("active_seconds_limit", lambda: budget.record_active("job", "test", elapsed))
    assert budget.snapshot("job", "test") == before


def test_record_active_counts_only_reported_intervals_across_human_wait_and_month(database, clock):
    budget = BrowserBudget(database)
    budget.record_active("job", "test", 1)
    clock[0] += timedelta(days=40)
    restarted = BrowserBudget(database)
    assert restarted.snapshot("job", "test")["active_microseconds"] == 1_000_000
    restarted.record_active("job", "test", 2)
    usage = restarted.snapshot("job", "test")
    assert usage["active_seconds"] == "3.000000"
    assert usage["actions"] == usage["model_calls"] == 0


@pytest.mark.parametrize("method", ["record_action", "record_active"])
def test_elapsed_time_is_rounded_up_and_clock_floats_are_supported(database, method):
    budget = BrowserBudget(database, max_active_seconds="0.000002", prices=PRICES)
    record = getattr(budget, method)
    record("job", "test", "0.0000001")
    record("job", "test", 0.000001)
    assert budget.snapshot("job", "test")["active_microseconds"] == 2
    assert budget.snapshot("job", "test")["actions"] == (2 if method == "record_action" else 0)
    assert_refused("active_seconds_limit", lambda: budget.record_action("job", "test"))
    assert_refused("active_seconds_limit", lambda: budget.record_active("job", "test", 0))


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"max_model_calls": 0}, "model_call_limit"),
        ({"max_actions": 0}, "action_limit"),
        ({"max_active_seconds": 0}, "active_seconds_limit"),
        ({"monthly_limit_nis": "0"}, "monthly_limit"),
        ({"application_limit_nis": "0"}, "application_limit"),
        ({"total_monthly_limit_nis": "0"}, "total_monthly_limit"),
    ],
)
def test_zero_caps_disable_work(database, options, reason):
    budget = BrowserBudget(database, prices=PRICES, **options)
    assert_refused(reason, lambda: budget.reserve("job", "test", "fixture-model", 0, 0))


@pytest.mark.parametrize(
    ("actual_input", "actual_output"),
    [
        (101, 100),
        (100, 201),
        (-1, 0),
        (0, -1),
        (1.0, 0),
        (True, 0),
        ("1", 0),
    ],
)
def test_invalid_actual_usage_preserves_full_hold(budget, actual_input, actual_output):
    reservation = budget.reserve("job", "test", "fixture-model", 100, 200)
    external = budget.reserve_external("fixture-model", 100, 200)
    before = budget.snapshot("job", "test")
    with pytest.raises(ValueError):
        budget.settle(reservation.reservation_id, actual_input, actual_output)
    with pytest.raises(ValueError):
        budget.settle_external(external.reservation_id, actual_input, actual_output)
    assert budget.snapshot("job", "test") == before


def test_unknown_outcomes_never_release_or_expire(database, clock):
    budget = BrowserBudget(database, prices=PRICES)
    reservation = reserve_one(budget)
    assert budget.settle(reservation.reservation_id, None, None) is None
    assert budget.settle(reservation.reservation_id, 0, None) is None
    clock[0] += timedelta(days=90)
    restarted = BrowserBudget(database, prices=PRICES)
    usage = restarted.snapshot("job", "test")
    assert usage["pending_reservations"] == 1
    assert usage["monthly_browser_micro_nis"] == 1_000_000
    assert usage["application_micro_nis"] == 1_000_000
    reserve_one(restarted)
    assert_refused("application_limit", lambda: reserve_one(restarted))


def test_missing_settlement_does_not_create_usage(budget):
    with pytest.raises(ValueError, match="not found"):
        budget.settle("unknown-reservation", 0, 0)
    assert budget.snapshot("job", "test")["model_calls"] == 0


@pytest.mark.parametrize(
    ("before", "after", "month"),
    [
        (
            datetime(2026, 1, 31, 21, 59, tzinfo=UTC),
            datetime(2026, 1, 31, 22, tzinfo=UTC),
            "2026-02",
        ),
        (
            datetime(2026, 9, 30, 20, 59, tzinfo=UTC),
            datetime(2026, 9, 30, 21, tzinfo=UTC),
            "2026-10",
        ),
    ],
)
def test_month_rollover_uses_hebron_and_carries_only_unresolved_holds(
    database, clock, before, after, month
):
    clock[0] = before
    budget = BrowserBudget(database, monthly_limit_nis="2", prices=PRICES)
    unresolved = reserve_one(budget, "unresolved")
    settled = reserve_one(budget, "settled")
    budget.settle(settled.reservation_id, 1_000_000, 0)
    previous_month = budget.snapshot("settled", "test")["billing_month"]
    clock[0] = after
    usage = budget.snapshot("settled", "test")
    assert usage["billing_month"] == month != previous_month
    assert usage["monthly_browser_micro_nis"] == 1_000_000
    assert usage["application_micro_nis"] == 1_000_000
    reserve_one(budget, "new-month")
    assert_refused("monthly_limit", lambda: reserve_one(budget, "extra"))
    # A late confirmation adjusts the original month, not the new month's charge.
    budget.settle(unresolved.reservation_id, 500_000, 0)
    assert budget.snapshot("unresolved", "test")["monthly_browser_micro_nis"] == 1_000_000
    with sqlite3.connect(database) as db:
        assert (
            db.execute(
                "SELECT billing_month FROM browser_budget_reservations WHERE reservation_id=?",
                (unresolved.reservation_id,),
            ).fetchone()[0]
            == previous_month
        )


def test_snapshot_contains_only_safe_aggregates_and_does_not_mutate(budget, database):
    application = "private-application-id"
    reserve_one(budget, application)
    budget.record_external_usage("private-event-key", "1")
    external = budget.reserve_external("fixture-model", 1_000_000, 0)
    snapshot = budget.snapshot(application, "test")
    serialized = json.dumps(snapshot)
    for secret in (
        application,
        "private-event-key",
        "fixture-model",
        str(database),
        external.reservation_id,
    ):
        assert secret not in serialized
    assert all(not isinstance(value, float) for value in snapshot.values())
    assert snapshot["monthly_total_micro_nis"] == 3_000_000
    assert snapshot["monthly_external_micro_nis"] == 2_000_000
    assert snapshot["monthly_external_token_micro_nis"] == 1_000_000
    empty = budget.snapshot("never-seen", "test")
    assert empty["model_calls"] == empty["actions"] == empty["application_micro_nis"] == 0
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM browser_budget_actions").fetchone()[0] == 0


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "bad", 0.1, True, "0.0000001"])
def test_invalid_money_configuration_is_rejected(database, value):
    with pytest.raises((ValueError, TypeError)):
        BrowserBudget(database, monthly_limit_nis=value)


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "bad", 0.1, True])
def test_invalid_model_prices_are_rejected(database, value):
    with pytest.raises((ValueError, TypeError)):
        BrowserBudget(
            database,
            prices={
                "fixture-model": {"input_nis_per_million": value, "output_nis_per_million": "1"}
            },
        )


@pytest.mark.parametrize("tokens", [-1, True, "1", 1.0, 2**63])
def test_invalid_token_limits_do_not_create_reservations(budget, tokens):
    with pytest.raises(ValueError):
        budget.reserve("job", "test", "fixture-model", tokens, 0)
    assert budget.snapshot("job", "test")["model_calls"] == 0


@pytest.mark.parametrize("elapsed", [-1, True, "NaN", float("inf"), "bad"])
def test_invalid_elapsed_time_does_not_create_actions(budget, elapsed):
    for record in (budget.record_action, budget.record_active):
        with pytest.raises((ValueError, TypeError)):
            record("job", "test", elapsed)
    assert budget.snapshot("job", "test")["actions"] == 0
    assert budget.snapshot("job", "test")["active_microseconds"] == 0


@pytest.mark.parametrize(
    ("application", "tracker"),
    [
        ("", "test"),
        ("  ", "test"),
        (None, "test"),
        ("job", "other"),
    ],
)
def test_invalid_identity_is_rejected(budget, application, tracker):
    with pytest.raises(ValueError):
        reserve_one(budget, application, tracker)
    with pytest.raises(ValueError):
        budget.record_action(application, tracker)
    with pytest.raises(ValueError):
        budget.record_active(application, tracker, 1)
    with pytest.raises(ValueError):
        budget.snapshot(application, tracker)


@pytest.mark.parametrize(
    ("options", "shared_application", "reason"),
    [
        ({"application_limit_nis": "1"}, True, "application_limit"),
        ({"monthly_limit_nis": "1"}, False, "monthly_limit"),
        ({"fixed_monthly_nis": "99"}, False, "total_monthly_limit"),
        ({"max_model_calls": 1}, True, "model_call_limit"),
    ],
)
def test_concurrent_reservations_cannot_overbook(database, options, shared_application, reason):
    barrier = Barrier(8)

    def attempt(index):
        budget = BrowserBudget(database, prices=PRICES, **options)
        barrier.wait(timeout=10)
        try:
            reserve_one(budget, "job" if shared_application else f"job-{index}")
            return "reserved"
        except BudgetExceeded as error:
            return error.reason_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert results.count("reserved") == 1
    assert results.count(reason) == 7
    assert BrowserBudget(database).snapshot("job", "test")["monthly_browser_micro_nis"] == 1_000_000


@pytest.mark.parametrize(
    ("options", "elapsed", "reason"),
    [
        ({"max_actions": 1}, 0, "action_limit"),
        ({"max_active_seconds": 1}, 1, "active_seconds_limit"),
    ],
)
def test_concurrent_actions_cannot_overbook(database, options, elapsed, reason):
    barrier = Barrier(8)

    def attempt(_):
        budget = BrowserBudget(database, **options)
        barrier.wait(timeout=10)
        try:
            budget.record_action("job", "test", elapsed)
            return "recorded"
        except BudgetExceeded as error:
            return error.reason_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert results.count("recorded") == 1
    assert results.count(reason) == 7
    assert BrowserBudget(database).snapshot("job", "test")["actions"] == 1


@pytest.mark.parametrize("mixed", [False, True])
def test_concurrent_active_time_and_actions_share_one_cap(database, mixed):
    barrier = Barrier(8)

    def attempt(index):
        budget = BrowserBudget(database, max_active_seconds=1, max_actions=40 if mixed else 0)
        method = "record_action" if mixed and index % 2 else "record_active"
        barrier.wait(timeout=10)
        try:
            getattr(budget, method)("job", "test", 1)
            return method
        except BudgetExceeded as error:
            return error.reason_code

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert results.count("active_seconds_limit") == 7
    usage = BrowserBudget(database).snapshot("job", "test")
    assert usage["actions"] == results.count("record_action")
    assert usage["active_microseconds"] == 1_000_000


def test_external_and_browser_usage_share_one_transactional_ceiling(database):
    barrier = Barrier(9)

    def attempt(index):
        budget = BrowserBudget(database, fixed_monthly_nis="99", prices=PRICES)
        barrier.wait(timeout=10)
        try:
            if index % 3 == 1:
                budget.record_external_usage(f"event-{index}", "1")
            elif index % 3 == 2:
                budget.reserve_external("fixture-model", 1_000_000, 0)
            else:
                reserve_one(budget, f"job-{index}")
            return "recorded"
        except BudgetExceeded as error:
            return error.reason_code

    with ThreadPoolExecutor(max_workers=9) as executor:
        results = list(executor.map(attempt, range(9)))
    assert results.count("recorded") == 1
    assert results.count("total_monthly_limit") == 8
    usage = BrowserBudget(database, fixed_monthly_nis="99").snapshot("job", "test")
    assert usage["monthly_total_micro_nis"] == 100_000_000


def test_concurrent_identical_settlements_are_idempotent(database, budget):
    reservation = reserve_one(budget)

    def settle(_):
        return BrowserBudget(database).settle(reservation.reservation_id, 500_000, 0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(settle, range(8))) == [500_000] * 8
    assert budget.snapshot("job", "test")["application_micro_nis"] == 500_000


def test_concurrent_conflicting_settlements_cannot_rewrite_usage(database, budget):
    reservation = reserve_one(budget)
    barrier = Barrier(2)

    def settle(tokens):
        barrier.wait(timeout=10)
        try:
            return BrowserBudget(database).settle(reservation.reservation_id, tokens, 0)
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(settle, [200_000, 300_000]))
    assert results.count("conflict") == 1
    winner = next(result for result in results if result != "conflict")
    assert budget.snapshot("job", "test")["application_micro_nis"] == winner


def test_concurrent_external_replays_charge_once(database):
    barrier = Barrier(8)

    def record(_):
        budget = BrowserBudget(database)
        barrier.wait(timeout=10)
        budget.record_external_usage("same-event", "60")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record, range(8)))
    assert BrowserBudget(database).snapshot("job", "test")["monthly_total_micro_nis"] == 60_000_000


def _process_reservation(path):
    budget = BrowserBudget(path, monthly_limit_nis="1", prices=PRICES)
    try:
        reserve_one(budget)
        return "reserved"
    except BudgetExceeded as error:
        return error.reason_code


def test_independent_processes_cannot_overbook(database):
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_process_reservation, [str(database)] * 8))
    assert results.count("reserved") == 1
    assert results.count("monthly_limit") == 7


def test_project_schema_can_be_added_idempotently_without_changing_existing_usage(database):
    budget = BrowserBudget(database, prices=PRICES)
    reserve_one(budget)
    budget.record_external_usage("old-cost-event", "1")
    before = budget.snapshot("job", "test")
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE project_budget_reservations")
        db.execute("CREATE TABLE parent_schema_versions (version INTEGER)")
        db.execute("INSERT INTO parent_schema_versions VALUES (5)")
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        install_budget_schema(db)
        install_budget_schema(db)
        assert db.in_transaction
        assert db.execute("SELECT * FROM parent_schema_versions").fetchall() == [(5,)]
    assert BrowserBudget(database).snapshot("job", "test") == before
    held = budget.reserve_external("fixture-model", 1_000_000, 0)
    assert held.reserved_micro_nis == 1_000_000


@pytest.mark.parametrize("disable_browser_caps", [False, True])
def test_external_token_reservations_ignore_all_browser_caps(database, disable_browser_caps):
    options = (
        {
            "monthly_limit_nis": "0",
            "application_limit_nis": "0",
            "max_model_calls": 0,
            "max_actions": 0,
            "max_active_seconds": 0,
        }
        if disable_browser_caps
        else {}
    )
    budget = BrowserBudget(database, prices=PRICES, **options)
    for _ in range(8):
        held = budget.reserve_external("fixture-model", 2_000_000, 2_000_000)
        assert held.reserved_micro_nis == 6_000_000
    usage = budget.snapshot("job", "test")
    assert usage["model_calls"] == usage["pending_reservations"] == usage["actions"] == 0
    assert usage["active_microseconds"] == usage["application_micro_nis"] == 0
    assert usage["monthly_browser_micro_nis"] == 0
    assert usage["monthly_external_model_calls"] == 8
    assert usage["monthly_external_pending_reservations"] == 8
    assert usage["monthly_external_token_micro_nis"] == 48_000_000
    assert usage["monthly_total_micro_nis"] == 48_000_000
    assert usage["monthly_external_reserved_input_tokens"] == 16_000_000
    assert usage["monthly_external_reserved_output_tokens"] == 16_000_000
    assert usage["monthly_external_input_tokens"] == usage["monthly_external_output_tokens"] == 0


def test_external_worst_case_shared_cap_and_confirmed_savings(database):
    budget = BrowserBudget(database, fixed_monthly_nis="98", prices=PRICES)
    reserve_one(budget)
    before = budget.snapshot("job", "test")
    assert_refused(
        "total_monthly_limit", lambda: budget.reserve_external("fixture-model", 1, 500_000)
    )
    assert budget.snapshot("job", "test") == before
    held = budget.reserve_external("fixture-model", 1_000_000, 0)
    before = budget.snapshot("job", "test")
    assert before["monthly_total_micro_nis"] == 100_000_000
    assert_refused("total_monthly_limit", lambda: reserve_one(budget, "other"))
    assert_refused("total_monthly_limit", lambda: budget.reserve_external("fixture-model", 1, 0))
    assert_refused("total_monthly_limit", lambda: budget.record_external_usage("extra", "0.1"))
    assert budget.snapshot("job", "test") == before
    assert budget.settle_external(held.reservation_id, 1, 0) == 1
    budget.reserve_external("fixture-model", 999_999, 0)
    assert budget.snapshot("job", "test")["monthly_total_micro_nis"] == 100_000_000
    with sqlite3.connect(database) as db:
        assert db.execute(
            "SELECT DISTINCT typeof(charged_micro_nis),typeof(input_price) "
            "FROM project_budget_reservations"
        ).fetchall() == [("integer", "text")]


def test_external_fixed_and_explicit_cost_events_are_in_shared_ceiling(database):
    budget = BrowserBudget(database, fixed_monthly_nis="97", prices=PRICES)
    budget.record_external_usage("explicit-event", "2")
    held = budget.reserve_external("fixture-model", 1_000_000, 0)
    assert held.reserved_micro_nis == 1_000_000
    usage = budget.snapshot("job", "test")
    assert usage["monthly_external_micro_nis"] == 3_000_000
    assert usage["monthly_external_token_micro_nis"] == 1_000_000
    assert usage["monthly_total_micro_nis"] == 100_000_000
    assert_refused("total_monthly_limit", lambda: budget.reserve_external("fixture-model", 1, 0))


def test_external_settlement_uses_stored_prices_and_actual_tokens_after_restart(database):
    original = BrowserBudget(database, prices=PRICES)
    held = original.reserve_external("fixture-model", 200_000, 400_000)
    repriced = BrowserBudget(
        database,
        prices={"fixture-model": {"input_nis_per_million": "100", "output_nis_per_million": "200"}},
    )
    assert repriced.settle_external(held.reservation_id, 100_000, 100_000) == 300_000
    restarted = BrowserBudget(database)
    usage = restarted.snapshot("job", "test")
    assert usage["monthly_external_micro_nis"] == usage["monthly_total_micro_nis"] == 300_000
    assert usage["monthly_external_input_tokens"] == 100_000
    assert usage["monthly_external_output_tokens"] == 100_000
    assert usage["monthly_external_reserved_input_tokens"] == 0
    assert usage["monthly_external_reserved_output_tokens"] == 0
    assert usage["monthly_external_model_calls"] == 1
    assert usage["monthly_external_pending_reservations"] == 0
    assert restarted.settle_external(held.reservation_id, 100_000, 100_000) == 300_000
    with pytest.raises(ValueError, match="Conflicting settlement"):
        restarted.settle_external(held.reservation_id, 0, 0)
    assert restarted.snapshot("job", "test") == usage


@pytest.mark.parametrize(("actual_input", "actual_output"), [(None, None), (0, None), (None, 0)])
def test_external_unknown_usage_never_expires_and_blocks_shared_cap(
    database, clock, actual_input, actual_output
):
    original = BrowserBudget(database, fixed_monthly_nis="99", prices=PRICES)
    held = original.reserve_external("fixture-model", 1_000_000, 0)
    assert original.settle_external(held.reservation_id, actual_input, actual_output) is None
    clock[0] += timedelta(days=90)
    restarted = BrowserBudget(database, fixed_monthly_nis="99", prices=PRICES)
    usage = restarted.snapshot("job", "test")
    assert usage["monthly_external_pending_reservations"] == 1
    assert usage["monthly_external_token_micro_nis"] == 1_000_000
    assert usage["monthly_external_reserved_input_tokens"] == 1_000_000
    assert usage["monthly_external_input_tokens"] == 0
    assert usage["monthly_total_micro_nis"] == 100_000_000
    assert_refused("total_monthly_limit", lambda: reserve_one(restarted))
    assert_refused("total_monthly_limit", lambda: restarted.reserve_external("fixture-model", 1, 0))
    assert restarted.snapshot("job", "test") == usage


@pytest.mark.parametrize(
    ("before", "after", "month"),
    [
        (
            datetime(2026, 1, 31, 21, 59, tzinfo=UTC),
            datetime(2026, 1, 31, 22, tzinfo=UTC),
            "2026-02",
        ),
        (
            datetime(2026, 9, 30, 20, 59, tzinfo=UTC),
            datetime(2026, 9, 30, 21, tzinfo=UTC),
            "2026-10",
        ),
    ],
)
def test_external_month_rollover_uses_hebron_and_late_settlement_keeps_original_month(
    database, clock, before, after, month
):
    clock[0] = before
    budget = BrowserBudget(database, total_monthly_limit_nis="2", prices=PRICES)
    pending = budget.reserve_external("fixture-model", 1_000_000, 0)
    settled = budget.reserve_external("fixture-model", 1_000_000, 0)
    budget.settle_external(settled.reservation_id, 1_000_000, 0)
    previous_month = budget.snapshot("job", "test")["billing_month"]
    clock[0] = after
    usage = budget.snapshot("job", "test")
    assert usage["billing_month"] == month != previous_month
    assert usage["monthly_external_model_calls"] == 1
    assert usage["monthly_external_pending_reservations"] == 1
    assert usage["monthly_external_input_tokens"] == 0
    assert usage["monthly_external_reserved_input_tokens"] == 1_000_000
    assert usage["monthly_total_micro_nis"] == 1_000_000
    new = budget.reserve_external("fixture-model", 1_000_000, 0)
    budget.settle_external(new.reservation_id, 750_000, 0)
    budget.settle_external(pending.reservation_id, 500_000, 0)
    usage = budget.snapshot("job", "test")
    assert usage["monthly_external_micro_nis"] == usage["monthly_total_micro_nis"] == 750_000
    assert usage["monthly_external_input_tokens"] == 750_000
    assert usage["monthly_external_reserved_input_tokens"] == 0
    with sqlite3.connect(database) as db:
        assert (
            db.execute(
                "SELECT billing_month FROM project_budget_reservations WHERE reservation_id=?",
                (pending.reservation_id,),
            ).fetchone()[0]
            == previous_month
        )


def test_browser_and_external_settlement_namespaces_do_not_cross(budget):
    browser = reserve_one(budget)
    external = budget.reserve_external("fixture-model", 1_000_000, 0)
    before = budget.snapshot("job", "test")
    for settle, reservation_id in (
        (budget.settle, external.reservation_id),
        (budget.settle_external, browser.reservation_id),
        (budget.settle_external, "missing-reservation"),
    ):
        with pytest.raises(ValueError, match="not found"):
            settle(reservation_id, 0, 0)
    assert budget.snapshot("job", "test") == before


@pytest.mark.parametrize("tokens", [-1, True, "1", 1.0, 2**63])
def test_invalid_external_token_limits_are_rejected_without_holds(budget, tokens):
    for input_tokens, output_tokens in ((tokens, 0), (0, tokens)):
        with pytest.raises(ValueError):
            budget.reserve_external("fixture-model", input_tokens, output_tokens)
    assert budget.snapshot("job", "test")["monthly_external_model_calls"] == 0


def test_external_zero_and_fractional_prices_share_exact_rounding_rules(database):
    budget = BrowserBudget(
        database,
        prices={
            "free": {"input_nis_per_million": "0", "output_nis_per_million": "0"},
            "fractional": {"input_nis_per_million": "0.4", "output_nis_per_million": "0.4"},
        },
    )
    free = budget.reserve_external("free", 100, 100)
    assert free.reserved_micro_nis == 0
    assert budget.settle_external(free.reservation_id, 100, 100) == 0
    fractional = budget.reserve_external("fractional", 1, 1)
    assert fractional.reserved_micro_nis == 1
    assert budget.settle_external(fractional.reservation_id, 1, 1) == 1
    assert budget.snapshot("job", "test")["monthly_total_micro_nis"] == 1


def test_concurrent_external_settlements_are_idempotent(database, budget):
    held = budget.reserve_external("fixture-model", 1_000_000, 0)

    def settle(_):
        return BrowserBudget(database).settle_external(held.reservation_id, 500_000, 0)

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(settle, range(8))) == [500_000] * 8
    assert budget.snapshot("job", "test")["monthly_external_micro_nis"] == 500_000
    assert budget.snapshot("job", "test")["monthly_external_input_tokens"] == 500_000


def test_concurrent_conflicting_external_settlements_do_not_rewrite_usage(database, budget):
    held = budget.reserve_external("fixture-model", 1_000_000, 0)
    barrier = Barrier(2)

    def settle(tokens):
        barrier.wait(timeout=10)
        try:
            return BrowserBudget(database).settle_external(held.reservation_id, tokens, 0)
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(settle, [200_000, 300_000]))
    assert results.count("conflict") == 1
    winner = next(result for result in results if result != "conflict")
    assert budget.snapshot("job", "test")["monthly_external_micro_nis"] == winner
    assert budget.snapshot("job", "test")["monthly_external_input_tokens"] == winner


def _process_external_reservation(path):
    budget = BrowserBudget(path, fixed_monthly_nis="99", prices=PRICES)
    try:
        budget.reserve_external("fixture-model", 1_000_000, 0)
        return "reserved"
    except BudgetExceeded as error:
        return error.reason_code


def test_independent_external_processes_cannot_overbook(database):
    with ProcessPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_process_external_reservation, [str(database)] * 8))
    assert results.count("reserved") == 1
    assert results.count("total_monthly_limit") == 7
    usage = BrowserBudget(database, fixed_monthly_nis="99").snapshot("job", "test")
    assert usage["monthly_external_pending_reservations"] == 1
    assert usage["monthly_total_micro_nis"] == 100_000_000
