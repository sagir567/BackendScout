import re
from dataclasses import dataclass

from backend_scout.models import (
    CandidateProfile,
    Job,
    LocationAssessment,
    MatchRecommendation,
    MatchResult,
    SalaryAssessment,
    ScoreBreakdownItem,
)

ROLE_RELEVANCE_MAX = 25
SKILL_OVERLAP_MAX = 30
EXPERIENCE_FIT_MAX = 15
WORK_MODE_LOCATION_MAX = 10
SALARY_FIT_MAX = 10
EVIDENCE_STRENGTH_MAX = 10

SKILL_ALIASES = {
    "api": {"api", "apis", "rest api", "rest apis", "backend api", "backend apis"},
    "fastapi": {"fastapi"},
    "python": {"python"},
    "mongodb": {"mongodb", "mongo db", "mongo"},
    "redis": {"redis"},
    "docker": {"docker", "containers", "containerization"},
    "azure": {"azure", "azure vm", "azure vms", "azure cloud"},
    "postgresql": {"postgresql", "postgres", "psql", "relational database", "relational databases"},
    "cosmos db": {"cosmos db", "azure cosmos db", "cosmos"},
    "backend systems": {"backend", "backend systems", "server side", "distributed backend"},
    "data pipelines": {"data pipeline", "data pipelines", "etl"},
    "c#": {"c#", "c sharp"},
    "dotnet": {"net", "net 8", "dotnet", "dotnet 8", "asp net core", "aspnet core"},
    "oop": {"oop", "object oriented programming", "object oriented"},
    "sql": {"sql", "postgresql", "postgres", "relational database", "relational databases"},
    "ci cd": {"ci cd", "cicd", "continuous integration", "continuous delivery"},
    "cloud": {"cloud", "cloud computing", "azure", "gcs", "google cloud storage"},
    "c++": {"c++", "cpp", "c plus plus"},
    "linux": {"linux", "linux based", "linux-based"},
    "git": {"git", "github", "version control"},
    "computer science degree": {
        "b sc computer science",
        "bsc computer science",
        "b sc computer science or equivalent",
        "bsc computer science or equivalent",
        "computer science degree",
        "computer science or equivalent",
    },
    "problem solving": {"problem solving", "problem-solving", "algorithmic thinking"},
    "large codebase": {
        "large codebase",
        "large code base",
        "large codebase comprehension",
        "large code base comprehension",
        "complex codebase",
        "complex code base",
    },
    "multithreading": {
        "multithreading",
        "multithreaded debugging",
        "multithreaded programming",
        "multithreaded programming and debugging",
        "multi threading",
        "multi threaded",
        "multi threaded debugging",
        "multi-threaded",
        "multi-threaded debugging",
        "concurrency",
        "threading",
    },
    "storage": {"storage", "data storage", "cloud storage"},
    "clustered systems": {"clustered systems", "clustered", "distributed systems"},
    "performance": {
        "performance",
        "performance oriented development",
        "performance-oriented development",
        "performance optimization",
    },
    "enterprise software": {"enterprise software", "enterprise class software", "enterprise-class software"},
}

BACKEND_SIGNAL_KEYWORDS = {
    "backend",
    "api",
    "apis",
    "c++",
    "fastapi",
    "linux",
    "microservice",
    "microservices",
    "server",
    "side",
    "database",
    "databases",
    "redis",
    "mongodb",
    "postgresql",
    "docker",
    "python",
}

UNKNOWN_SALARY_MARKERS = {"not listed", "not provided", "unknown", "competitive", "tbd", "n/a"}
ISRAEL_LOCATION_TERMS = {
    "israel",
    "tel aviv",
    "telaviv",
    "herzliya",
    "haifa",
    "jerusalem",
    "raanana",
    "netanya",
    "petah tikva",
    "rishon lezion",
    "beer sheva",
}


@dataclass(frozen=True)
class ScoredJob:
    job: Job
    result: MatchResult


def score_jobs(profile: CandidateProfile, jobs: list[Job]) -> list[ScoredJob]:
    return [ScoredJob(job=job, result=score_job(profile, job)) for job in jobs]


def score_job(profile: CandidateProfile, job: Job) -> MatchResult:
    strengths: list[str] = []
    concerns: list[str] = []
    matched_skills, missing_skills, skill_item = _score_skill_overlap(profile, job, strengths, concerns)
    role_item, role_is_hard_blocker = _score_role_relevance(profile, job, strengths, concerns)
    experience_item = _score_experience_fit(profile, job, concerns)
    work_item, location_assessment, location_hard_blocker = _score_work_mode_and_location(
        profile,
        job,
        strengths,
        concerns,
    )
    salary_item, salary_assessment = _score_salary_fit(profile, job, concerns)
    evidence_item = _score_evidence_strength(profile, job, matched_skills, strengths, concerns)

    breakdown = [
        role_item,
        skill_item,
        experience_item,
        work_item,
        salary_item,
        evidence_item,
    ]
    match_score = sum(item.points_awarded for item in breakdown)

    hard_blocker = role_is_hard_blocker or location_hard_blocker
    recommended_action = _recommended_action(match_score, hard_blocker)
    reason_summary = _reason_summary(strengths, concerns, recommended_action)

    return MatchResult(
        match_score=match_score,
        recommended_action=recommended_action,
        matched_skills=matched_skills,
        missing_skills=missing_skills,
        strengths=strengths,
        concerns=concerns,
        score_breakdown=breakdown,
        salary_assessment=salary_assessment,
        location_assessment=location_assessment,
        reason_summary=reason_summary,
    )


def _score_role_relevance(
    profile: CandidateProfile,
    job: Job,
    strengths: list[str],
    concerns: list[str],
) -> tuple[ScoreBreakdownItem, bool]:
    title = _normalize_text(job.title)
    title_tokens = _keyword_set(job.title)
    description_tokens = _keyword_set(job.description)
    target_role_hits = [
        role
        for role in profile.target_roles
        if _roles_match(_normalize_text(role), title)
    ]

    roles_to_avoid = [_normalize_text(role) for role in profile.constraints.roles_to_avoid]
    if any(role and role in title for role in roles_to_avoid):
        concerns.append("Role matches your avoid list.")
        return (
            ScoreBreakdownItem(
                component="role_relevance",
                points_awarded=0,
                points_max=ROLE_RELEVANCE_MAX,
                reason="Title matches a role_to_avoid entry.",
            ),
            True,
        )

    has_backend_title = "backend" in title or ("python" in title and "engineer" in title)
    has_backend_signals = bool((title_tokens | description_tokens) & BACKEND_SIGNAL_KEYWORDS)
    has_adjacent_software_title = "software engineer" in title or "software developer" in title

    direct_backend_target_hit = any(
        "backend" in _normalize_text(role) or "python" in _normalize_text(role)
        for role in target_role_hits
    )

    if target_role_hits and has_backend_signals:
        strengths.append("Title aligns directly with your target roles and the description has backend signals.")
        return (
            ScoreBreakdownItem(
                component="role_relevance",
                points_awarded=ROLE_RELEVANCE_MAX,
                points_max=ROLE_RELEVANCE_MAX,
                reason="Direct target-role match with backend or server-side signals.",
            ),
            False,
        )

    if direct_backend_target_hit or has_backend_title:
        strengths.append("Title aligns directly with your backend target roles.")
        return (
            ScoreBreakdownItem(
                component="role_relevance",
                points_awarded=ROLE_RELEVANCE_MAX,
                points_max=ROLE_RELEVANCE_MAX,
                reason="Direct backend or target-role title match.",
            ),
            False,
        )

    if has_adjacent_software_title and has_backend_signals:
        strengths.append("Software-engineer role includes backend signals in the job description.")
        return (
            ScoreBreakdownItem(
                component="role_relevance",
                points_awarded=18,
                points_max=ROLE_RELEVANCE_MAX,
                reason="Adjacent software role with meaningful backend signals.",
            ),
            False,
        )

    if has_adjacent_software_title:
        concerns.append("Software-engineer title matches broadly, but backend signals are weak.")
        return (
            ScoreBreakdownItem(
                component="role_relevance",
                points_awarded=10,
                points_max=ROLE_RELEVANCE_MAX,
                reason="Generic software-engineer title without strong backend evidence.",
            ),
            False,
        )

    if has_backend_signals:
        concerns.append("Role title is broad, but the description still shows backend work.")
        return (
            ScoreBreakdownItem(
                component="role_relevance",
                points_awarded=10,
                points_max=ROLE_RELEVANCE_MAX,
                reason="Backend relevance is present but not central in the title.",
            ),
            False,
        )

    concerns.append("Role does not look backend-oriented.")
    return (
        ScoreBreakdownItem(
            component="role_relevance",
            points_awarded=0,
            points_max=ROLE_RELEVANCE_MAX,
            reason="No clear backend relevance in the title or description.",
        ),
        True,
    )


def _score_skill_overlap(
    profile: CandidateProfile,
    job: Job,
    strengths: list[str],
    concerns: list[str],
) -> tuple[list[str], list[str], ScoreBreakdownItem]:
    if not job.required_skills:
        concerns.append("Job did not list required skills explicitly.")
        return (
            [],
            [],
            ScoreBreakdownItem(
                component="skill_overlap",
                points_awarded=15,
                points_max=SKILL_OVERLAP_MAX,
                reason="No required_skills were provided, so the score stays neutral.",
            ),
        )

    core_skills = {_canonical_skill(skill) for skill in profile.core_skills}
    nice_skills = {_canonical_skill(skill) for skill in profile.nice_to_have_skills}

    matched_skills: list[str] = []
    missing_skills: list[str] = []
    weighted_matches = 0.0
    for skill in job.required_skills:
        canonical_skill = _canonical_skill(skill)
        if canonical_skill in core_skills:
            matched_skills.append(skill)
            weighted_matches += 1.0
        elif canonical_skill in nice_skills:
            matched_skills.append(skill)
            weighted_matches += 0.5
        else:
            missing_skills.append(skill)

    points_awarded = round(SKILL_OVERLAP_MAX * (weighted_matches / len(job.required_skills)))
    points_awarded = max(0, min(SKILL_OVERLAP_MAX, points_awarded))

    if matched_skills:
        strengths.append(f"Matched required skills: {', '.join(matched_skills[:4])}.")
    if missing_skills:
        concerns.append(f"Missing or unproven skills: {', '.join(missing_skills[:4])}.")

    return (
        matched_skills,
        missing_skills,
        ScoreBreakdownItem(
            component="skill_overlap",
            points_awarded=points_awarded,
            points_max=SKILL_OVERLAP_MAX,
            reason=(
                f"Matched {len(matched_skills)} of {len(job.required_skills)} listed skills "
                "with core skills weighted more heavily than nice-to-have skills."
            ),
        ),
    )


def _score_experience_fit(
    profile: CandidateProfile,
    job: Job,
    concerns: list[str],
) -> ScoreBreakdownItem:
    required_years = _extract_minimum_years(job.years_experience)
    if required_years is None:
        return ScoreBreakdownItem(
            component="experience_fit",
            points_awarded=8,
            points_max=EXPERIENCE_FIT_MAX,
            reason="Years of experience were not clearly stated.",
        )

    if profile.seniority.max_years is None:
        return ScoreBreakdownItem(
            component="experience_fit",
            points_awarded=EXPERIENCE_FIT_MAX,
            points_max=EXPERIENCE_FIT_MAX,
            reason="Candidate profile allows open-ended seniority on the high end.",
        )

    if profile.seniority.min_years <= required_years <= profile.seniority.max_years:
        return ScoreBreakdownItem(
            component="experience_fit",
            points_awarded=EXPERIENCE_FIT_MAX,
            points_max=EXPERIENCE_FIT_MAX,
            reason="Required experience fits within your current target range.",
        )

    if required_years == profile.seniority.max_years + 1:
        concerns.append("Job asks for slightly more experience than your current target range.")
        return ScoreBreakdownItem(
            component="experience_fit",
            points_awarded=8,
            points_max=EXPERIENCE_FIT_MAX,
            reason="Required experience is only slightly above your target range.",
        )

    if required_years > profile.seniority.max_years + 1:
        concerns.append("Job asks for materially more experience than your current target range.")
        return ScoreBreakdownItem(
            component="experience_fit",
            points_awarded=3,
            points_max=EXPERIENCE_FIT_MAX,
            reason="Required experience is well above your target range.",
        )

    return ScoreBreakdownItem(
        component="experience_fit",
        points_awarded=12,
        points_max=EXPERIENCE_FIT_MAX,
        reason="Required experience is below your target range, which is still acceptable for screening.",
    )


def _score_work_mode_and_location(
    profile: CandidateProfile,
    job: Job,
    strengths: list[str],
    concerns: list[str],
) -> tuple[ScoreBreakdownItem, LocationAssessment, bool]:
    remote_mode = _classify_remote_policy(job.remote_policy)
    location_assessment = LocationAssessment.UNKNOWN
    hard_blocker = False

    if remote_mode == "onsite" and not profile.work_preferences.onsite:
        concerns.append("Job appears onsite-only, but your profile currently disallows onsite work.")
        return (
            ScoreBreakdownItem(
                component="work_mode_location",
                points_awarded=0,
                points_max=WORK_MODE_LOCATION_MAX,
                reason="Onsite-only role conflicts with your work preferences.",
            ),
            LocationAssessment.MISMATCH,
            True,
        )

    if remote_mode == "remote" and profile.work_preferences.remote:
        strengths.append("Remote policy matches your preferred work mode.")
        return (
            ScoreBreakdownItem(
                component="work_mode_location",
                points_awarded=WORK_MODE_LOCATION_MAX,
                points_max=WORK_MODE_LOCATION_MAX,
                reason="Remote work is explicitly allowed and preferred.",
            ),
            LocationAssessment.FIT,
            False,
        )

    if remote_mode == "hybrid" and profile.work_preferences.hybrid:
        if not job.location:
            concerns.append("Hybrid role fits your work mode, but the office location is missing.")
            return (
                ScoreBreakdownItem(
                    component="work_mode_location",
                    points_awarded=5,
                    points_max=WORK_MODE_LOCATION_MAX,
                    reason="Hybrid work matches, but location details are incomplete.",
                ),
                LocationAssessment.UNKNOWN,
                False,
            )

        if _location_matches(profile, job.location):
            strengths.append("Hybrid role matches your preferred work mode and target locations.")
            location_assessment = LocationAssessment.FIT
            points_awarded = WORK_MODE_LOCATION_MAX
            reason = "Hybrid work matches your preferences and listed target locations."
        else:
            concerns.append("Hybrid role fits your work mode, but the location may be inconvenient.")
            location_assessment = LocationAssessment.MISMATCH
            points_awarded = 4
            reason = "Hybrid work matches, but the location is outside your target locations."
        return (
            ScoreBreakdownItem(
                component="work_mode_location",
                points_awarded=points_awarded,
                points_max=WORK_MODE_LOCATION_MAX,
                reason=reason,
            ),
            location_assessment,
            False,
        )

    if remote_mode == "onsite":
        if _location_matches(profile, job.location):
            strengths.append("Job location matches one of your target locations.")
            return (
                ScoreBreakdownItem(
                    component="work_mode_location",
                    points_awarded=6,
                    points_max=WORK_MODE_LOCATION_MAX,
                    reason="Onsite work is allowed and the location matches your targets.",
                ),
                LocationAssessment.FIT,
                False,
            )

        concerns.append("Onsite role location is outside your current target locations.")
        return (
            ScoreBreakdownItem(
                component="work_mode_location",
                points_awarded=2,
                points_max=WORK_MODE_LOCATION_MAX,
                reason="Onsite role is outside your target locations.",
            ),
            LocationAssessment.MISMATCH,
            False,
        )

    if job.location and _location_matches(profile, job.location):
        strengths.append("Job location matches your target locations.")
        return (
            ScoreBreakdownItem(
                component="work_mode_location",
                points_awarded=7,
                points_max=WORK_MODE_LOCATION_MAX,
                reason="Location matches even though remote policy is not fully specified.",
            ),
            LocationAssessment.FIT,
            False,
        )

    concerns.append("Remote policy or location is incomplete, so fit stays partially unknown.")
    return (
        ScoreBreakdownItem(
            component="work_mode_location",
            points_awarded=5,
            points_max=WORK_MODE_LOCATION_MAX,
            reason="Remote policy or location was not specific enough for a stronger score.",
        ),
        LocationAssessment.UNKNOWN,
        hard_blocker,
    )


def _score_salary_fit(
    profile: CandidateProfile,
    job: Job,
    concerns: list[str],
) -> tuple[ScoreBreakdownItem, SalaryAssessment]:
    minimum_salary = _extract_salary_floor(job.salary_text)
    if minimum_salary is None:
        return (
            ScoreBreakdownItem(
                component="salary_fit",
                points_awarded=5,
                points_max=SALARY_FIT_MAX,
                reason="Salary was not listed clearly enough to compare against your floor.",
            ),
            SalaryAssessment.UNKNOWN,
        )

    if minimum_salary >= profile.salary_floor_nis:
        return (
            ScoreBreakdownItem(
                component="salary_fit",
                points_awarded=SALARY_FIT_MAX,
                points_max=SALARY_FIT_MAX,
                reason=f"Listed salary meets or exceeds your {profile.salary_floor_nis} NIS floor.",
            ),
            SalaryAssessment.ABOVE_FLOOR,
        )

    ratio = minimum_salary / profile.salary_floor_nis if profile.salary_floor_nis else 0
    if ratio >= 0.9:
        points_awarded = 4
    elif ratio >= 0.75:
        points_awarded = 2
    else:
        points_awarded = 0

    concerns.append(
        f"Listed salary appears below your floor ({minimum_salary:,} NIS vs {profile.salary_floor_nis:,} NIS)."
    )
    return (
        ScoreBreakdownItem(
            component="salary_fit",
            points_awarded=points_awarded,
            points_max=SALARY_FIT_MAX,
            reason=f"Listed salary falls below your {profile.salary_floor_nis} NIS floor.",
        ),
        SalaryAssessment.BELOW_FLOOR,
    )


def _score_evidence_strength(
    profile: CandidateProfile,
    job: Job,
    matched_skills: list[str],
    strengths: list[str],
    concerns: list[str],
) -> ScoreBreakdownItem:
    if not profile.proof_points:
        concerns.append("Candidate profile still needs proof points to support CV tailoring.")
        return ScoreBreakdownItem(
            component="evidence_strength",
            points_awarded=0,
            points_max=EVIDENCE_STRENGTH_MAX,
            reason="No proof_points were available for evidence matching.",
        )

    evidence_targets = matched_skills or job.required_skills or [job.title]
    matched_evidence = 0
    for target in evidence_targets:
        if _proof_points_support(profile.proof_points, target):
            matched_evidence += 1

    if not evidence_targets:
        return ScoreBreakdownItem(
            component="evidence_strength",
            points_awarded=5,
            points_max=EVIDENCE_STRENGTH_MAX,
            reason="Proof points exist, but the job description did not expose concrete targets to compare.",
        )

    points_awarded = round(EVIDENCE_STRENGTH_MAX * (matched_evidence / len(evidence_targets)))
    points_awarded = max(0, min(EVIDENCE_STRENGTH_MAX, points_awarded))

    if matched_evidence:
        strengths.append("Your proof points support part of this role's skill or domain profile.")
    else:
        concerns.append("The job asks for skills that are not yet strongly tied to your proof points.")

    return ScoreBreakdownItem(
        component="evidence_strength",
        points_awarded=points_awarded,
        points_max=EVIDENCE_STRENGTH_MAX,
        reason=f"{matched_evidence} of {len(evidence_targets)} evidence targets are supported by proof points.",
    )


def _recommended_action(score: int, hard_blocker: bool) -> MatchRecommendation:
    if hard_blocker or score < 55:
        return MatchRecommendation.SKIP
    if score >= 75:
        return MatchRecommendation.APPLY
    return MatchRecommendation.MAYBE


def _reason_summary(
    strengths: list[str],
    concerns: list[str],
    recommendation: MatchRecommendation,
) -> str:
    summary_parts = [f"Recommendation: {recommendation.value}."]
    if strengths:
        summary_parts.append(strengths[0])
    if concerns:
        summary_parts.append(f"Concern: {concerns[0]}")
    return " ".join(summary_parts)


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(re.findall(r"[a-z0-9+#]+", value.lower()))


def _keyword_set(value: str | None) -> set[str]:
    return set(_normalize_text(value).split())


def _canonical_skill(skill: str) -> str:
    normalized_skill = _normalize_text(skill)
    for canonical_name, aliases in SKILL_ALIASES.items():
        if normalized_skill == canonical_name or normalized_skill in aliases:
            return canonical_name
    return normalized_skill


def _roles_match(normalized_target_role: str, normalized_title: str) -> bool:
    if normalized_target_role in normalized_title or normalized_title in normalized_target_role:
        return True
    software_roles = {"software engineer", "software developer"}
    return normalized_target_role in software_roles and any(
        role in normalized_title for role in software_roles
    )


def _extract_minimum_years(years_text: str | None) -> int | None:
    if not years_text:
        return None

    matches = [int(match) for match in re.findall(r"\d+", years_text)]
    if not matches:
        return None
    return min(matches)


def _classify_remote_policy(remote_policy: str | None) -> str | None:
    normalized_policy = _normalize_text(remote_policy)
    if not normalized_policy:
        return None

    if "hybrid" in normalized_policy:
        return "hybrid"
    if "remote" in normalized_policy or "from home" in normalized_policy:
        return "remote"
    if "onsite" in normalized_policy or "on site" in normalized_policy or "office" in normalized_policy:
        return "onsite"
    return None


def _location_matches(profile: CandidateProfile, location: str | None) -> bool:
    normalized_location = _normalize_text(location)
    if not normalized_location:
        return False

    for target_location in profile.target_locations:
        normalized_target = _normalize_text(target_location)
        if not normalized_target:
            continue
        if normalized_target == "remote":
            continue
        if normalized_target in normalized_location or normalized_location in normalized_target:
            return True
        if normalized_target == "israel" and any(
            term in normalized_location for term in ISRAEL_LOCATION_TERMS
        ):
            return True

    return False


def _extract_salary_floor(salary_text: str | None) -> int | None:
    if not salary_text:
        return None

    lowered_salary = salary_text.lower().strip()
    normalized_salary = _normalize_text(salary_text)
    if not normalized_salary or lowered_salary in UNKNOWN_SALARY_MARKERS:
        return None

    matches = re.findall(r"(\d{1,3}(?:[,\s]\d{3})+|\d+(?:[.,]\d+)?)\s*([kK]?)", salary_text)
    salaries: list[int] = []
    for number_text, suffix in matches:
        cleaned_number = number_text.replace(",", "").replace(" ", "")
        numeric_value = float(cleaned_number)
        if suffix.lower() == "k":
            numeric_value *= 1000
        elif numeric_value < 1000:
            continue
        salaries.append(round(numeric_value))

    if not salaries:
        return None
    return min(salaries)


def _proof_points_support(proof_points: list[str], target: str) -> bool:
    canonical_target = _canonical_skill(target)
    target_tokens = _keyword_set(target)
    target_aliases = SKILL_ALIASES.get(canonical_target, {canonical_target})

    for proof_point in proof_points:
        normalized_proof = _normalize_text(proof_point)
        proof_tokens = _keyword_set(proof_point)
        if any(alias in normalized_proof for alias in target_aliases):
            return True
        if canonical_target in proof_tokens:
            return True
        if target_tokens and target_tokens <= proof_tokens:
            return True

    return False
