from pathlib import Path

import pytest

from backend_scout.agent_gateway import AGENT_INSTRUCTIONS, run_agent_chat


def test_agent_gateway_uses_advisory_boundary(tmp_path: Path) -> None:
    seen: list[str] = []
    response = run_agent_chat(
        "What should I do next?",
        "telegram-1",
        tmp_path / "runtime.sqlite3",
        "test-key",
        "test-model",
        runner=lambda prompt: seen.append(prompt) or "Approve the CV next.",
    )

    assert response == "Approve the CV next."
    assert seen == ["What should I do next?"]
    assert "Never claim that an application was submitted" in AGENT_INSTRUCTIONS


def test_agent_gateway_rejects_empty_messages(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        run_agent_chat(" ", "telegram-1", tmp_path / "runtime.sqlite3", "key", "model")
