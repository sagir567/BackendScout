from types import SimpleNamespace

import pytest

from backend_scout.verification_handoff import (
    HandoffAction,
    VerificationHandoffServer,
    _parse_action,
    apply_handoff_actions,
    is_tailscale_address,
)


def test_tailscale_address_validation_rejects_public_and_lan_addresses() -> None:
    assert is_tailscale_address("100.64.0.1")
    assert is_tailscale_address("100.127.255.254")
    assert is_tailscale_address("fd7a:115c:a1e0::1")
    assert not is_tailscale_address("192.168.1.10")
    assert not is_tailscale_address("8.8.8.8")


def test_handoff_server_refuses_non_tailscale_bindings() -> None:
    with pytest.raises(ValueError, match="Tailscale address"):
        VerificationHandoffServer("0.0.0.0")


def test_handoff_action_parser_validates_remote_input() -> None:
    assert _parse_action({"kind": "click", "x": 125.5, "y": 77.25}) == HandoffAction(
        kind="click", x=125.5, y=77.25
    )
    assert _parse_action({"kind": "scroll", "delta_y": 50_000}) == HandoffAction(
        kind="scroll", delta_y=1_000
    )
    with pytest.raises(ValueError, match="Key is not allowed"):
        _parse_action({"kind": "key", "key": "Meta+Q"})
    with pytest.raises(ValueError, match="Text is not allowed"):
        _parse_action({"kind": "type", "text": "line one\nline two"})


def test_handoff_actions_are_applied_on_the_playwright_thread() -> None:
    calls: list[tuple[object, ...]] = []
    page = SimpleNamespace(
        mouse=SimpleNamespace(
            click=lambda x, y: calls.append(("click", x, y)),
            wheel=lambda x, y: calls.append(("wheel", x, y)),
        ),
        keyboard=SimpleNamespace(
            press=lambda key: calls.append(("press", key)),
            type=lambda text: calls.append(("type", text)),
        ),
    )

    apply_handoff_actions(
        page,
        (
            HandoffAction(kind="click", x=10, y=20),
            HandoffAction(kind="scroll", delta_y=650),
            HandoffAction(kind="key", key="Enter"),
            HandoffAction(kind="type", text="human input"),
        ),
    )

    assert calls == [
        ("click", 10, 20),
        ("wheel", 0, 650),
        ("press", "Enter"),
        ("type", "human input"),
    ]
