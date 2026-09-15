"""Persistent, advisory-only agent conversations for Telegram."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

AGENT_INSTRUCTIONS = """You are BackendScout's conversational career assistant.
Answer concisely and practically. You may explain tracked workflow states, suggest
truthful CV emphasis, ask the candidate for missing facts, and explain next steps.
Never claim that a job, CV, email, tracker, browser, or application was changed.
Never claim that an application was submitted. You have no tools and produce advice
only; deterministic BackendScout commands and approval gates execute all actions.
Never invent candidate experience or employer details."""


def run_agent_chat(
    prompt: str,
    session_id: str,
    database_path: Path,
    api_key: str,
    model: str,
    *,
    runner: Callable[[str], str] | None = None,
) -> str:
    if not prompt.strip():
        raise ValueError("Agent message cannot be empty")
    if runner is not None:
        return runner(prompt)

    from agents import Agent, Runner, SQLiteSession, set_default_openai_key

    set_default_openai_key(api_key)
    agent = Agent(name="BackendScout Advisor", instructions=AGENT_INSTRUCTIONS, model=model)
    session = SQLiteSession(
        f"backendscout:{session_id}",
        db_path=database_path,
        sessions_table="assistant_sessions",
        messages_table="assistant_messages",
    )
    result = Runner.run_sync(agent, prompt, session=session, max_turns=2)
    output = result.final_output
    if not isinstance(output, str) or not output.strip():
        raise ValueError("BackendScout advisor returned no text")
    return output.strip()
