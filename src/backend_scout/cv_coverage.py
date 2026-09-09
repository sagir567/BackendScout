"""Deterministic CV evidence coverage checks.

This is intentionally separate from job matching. A tailored CV can improve
clarity and relevance, but it must never manufacture a higher job match score.
"""

import re

from backend_scout.models import CareerEvidence, CvEvidenceCoverage, Job

CV_EVIDENCE_COVERAGE_TARGET = 90

_TOKEN_PATTERN = re.compile(r"[a-z0-9+#.]+")

_REQUIREMENT_ALIASES: dict[str, set[str]] = {
    "software development": {
        "software development",
        "software developer",
        "programming",
        "python",
        "c#",
        "java",
        "c++",
        ".net",
    },
    "software design": {
        "software design",
        "design patterns",
        "clean code",
        "modular architecture",
    },
    "system design": {
        "system design",
        "system architecture",
        "architecture",
        "data modeling",
        "requirement analysis",
    },
    "ci/cd": {"ci/cd", "cicd", "continuous integration", "continuous delivery"},
    "containers": {"container", "containers", "docker", "docker compose", "container registry"},
    "cloud infrastructure": {
        "cloud infrastructure",
        "azure",
        "azure web apps",
        "azure container registry",
        "azure virtual machines",
        "google cloud storage",
        "gcs",
    },
    "ai tools": {
        "ai tools",
        "codex",
        "cline",
        "ollama",
        "openai api",
        "llm tooling",
        "agent orchestration",
    },
    "b.sc. computer science or equivalent": {
        "b.sc. in computer science",
        "b.sc. in computer science and mathematics",
        "bsc computer science",
        "computer science and mathematics",
        "computer science",
    },
    "bsc computer science or equivalent": {
        "b.sc. in computer science",
        "b.sc. in computer science and mathematics",
        "bsc computer science",
        "computer science and mathematics",
        "computer science",
    },
    "b sc computer science or equivalent": {
        "b.sc. in computer science",
        "b.sc. in computer science and mathematics",
        "bsc computer science",
        "computer science and mathematics",
        "computer science",
    },
    "problem solving": {
        "problem solving",
        "algorithmic thinking",
        "debugging",
        "requirements analysis",
    },
    "large codebase comprehension": {
        "large codebase comprehension",
        "existing code",
        "existing codebase",
        "refactoring",
        "debugging",
        "requirements analysis",
    },
    "large code base comprehension": {
        "large codebase comprehension",
        "existing code",
        "existing codebase",
        "refactoring",
        "debugging",
        "requirements analysis",
    },
    "linux": {
        "linux",
        "linux-based",
        "linux based",
        "hpc execution",
        "high-performance computing",
    },
    "multi-threaded debugging": {
        "multithreaded programming and debugging",
        "multi-threaded debugging",
        "multithreading",
        "concurrency",
    },
    "multi threaded debugging": {
        "multithreaded programming and debugging",
        "multi-threaded debugging",
        "multithreading",
        "concurrency",
    },
    "multithreaded debugging": {
        "multithreaded programming and debugging",
        "multi-threaded debugging",
        "multithreading",
        "concurrency",
    },
}

_CLARIFICATION_QUESTIONS: dict[str, str] = {
    "distributed systems": (
        "What direct experience can we document with components running across multiple "
        "machines or services, such as coordination, queues, retries, service-to-service "
        "communication, or distributed data processing?"
    ),
    "kubernetes": (
        "Do you have completed hands-on Kubernetes experience that we can document truthfully? "
        "If not, this stays a gap until a real project is completed."
    ),
    "english": (
        "Can we state a specific English level on the CV, such as professional working proficiency? "
        "Please confirm only the level you can support in an interview."
    ),
}


def assess_cv_evidence_coverage(evidence: CareerEvidence, job: Job) -> CvEvidenceCoverage:
    """Report how much of a job's explicit requirements the evidence can support."""
    requirements = list(dict.fromkeys(job.required_skills))
    if not requirements:
        return CvEvidenceCoverage(
            coverage_score=100,
            target_score=CV_EVIDENCE_COVERAGE_TARGET,
            covered_requirements=[],
            missing_requirements=[],
            supporting_evidence_ids=[],
            clarification_questions=[],
            summary="The job lists no explicit required skills, so there is no requirement gap to measure.",
        )

    evidence_text_by_id = _evidence_text_by_id(evidence)
    covered: list[str] = []
    missing: list[str] = []
    supporting_ids: list[str] = []
    for requirement in requirements:
        matched_ids = _supporting_evidence_ids(requirement, evidence_text_by_id)
        if matched_ids:
            covered.append(requirement)
            supporting_ids.extend(matched_ids)
        else:
            missing.append(requirement)

    score = round((len(covered) / len(requirements)) * 100)
    questions = [_clarification_question(requirement) for requirement in missing]
    summary = (
        f"Evidence supports {len(covered)} of {len(requirements)} explicit job requirements "
        f"({score}/100)."
    )
    return CvEvidenceCoverage(
        coverage_score=score,
        target_score=CV_EVIDENCE_COVERAGE_TARGET,
        covered_requirements=covered,
        missing_requirements=missing,
        supporting_evidence_ids=list(dict.fromkeys(supporting_ids)),
        clarification_questions=questions,
        summary=summary,
    )


def _evidence_text_by_id(evidence: CareerEvidence) -> dict[str, str]:
    source: dict[str, str] = {"summary": evidence.summary or ""}
    source["skills"] = " ".join(item for group in evidence.skills for item in group.items)
    for experience in evidence.experience:
        source[experience.id] = " ".join(
            [experience.organization, experience.title, *(bullet.text for bullet in experience.bullets)]
        )
        source.update({bullet.id: bullet.text for bullet in experience.bullets})
    for project in evidence.projects:
        source[project.id] = " ".join([project.name, *(bullet.text for bullet in project.bullets)])
        source.update({bullet.id: bullet.text for bullet in project.bullets})
    for education in evidence.education:
        source[f"education:{education.institution}:{education.credential}"] = " ".join(
            [education.institution, education.credential, education.end_date or ""]
        )
    for publication in evidence.publications:
        source[f"publication:{publication.title}"] = " ".join(
            [publication.title, publication.venue, publication.year or ""]
        )
    return source


def _supporting_evidence_ids(requirement: str, evidence_text_by_id: dict[str, str]) -> list[str]:
    normalized_requirement = _normalize(requirement)
    candidates = _REQUIREMENT_ALIASES.get(normalized_requirement, {normalized_requirement})
    return [
        evidence_id
        for evidence_id, text in evidence_text_by_id.items()
        if any(_contains_phrase(text, candidate) for candidate in candidates)
    ]


def _clarification_question(requirement: str) -> str:
    normalized_requirement = _normalize(requirement)
    return _CLARIFICATION_QUESTIONS.get(
        normalized_requirement,
        f"What factual experience, completed project, or verifiable proof can support the requirement: {requirement}?",
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    text_tokens = set(_TOKEN_PATTERN.findall(text.casefold()))
    phrase_tokens = set(_TOKEN_PATTERN.findall(phrase.casefold()))
    return bool(phrase_tokens) and phrase_tokens.issubset(text_tokens)


def _normalize(value: str) -> str:
    return " ".join(_TOKEN_PATTERN.findall(value.casefold()))
