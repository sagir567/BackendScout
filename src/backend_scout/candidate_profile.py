from pathlib import Path

from backend_scout.models import CandidateProfile
from backend_scout.yaml_files import load_yaml_mapping

DEFAULT_PROFILE_PATH = Path("config/candidate_profile.yaml")


def load_candidate_profile(path: Path = DEFAULT_PROFILE_PATH) -> CandidateProfile:
    return CandidateProfile.model_validate(load_yaml_mapping(path))


def candidate_profile_warnings(profile: CandidateProfile) -> list[str]:
    warnings: list[str] = []
    if not profile.proof_points:
        warnings.append("Add proof_points so future CV tailoring can stay specific and truthful.")
    if not profile.target_locations:
        warnings.append("Add target_locations so matching can filter location and remote policy.")
    return warnings
