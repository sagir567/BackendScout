from pathlib import Path

from backend_scout.models import CareerEvidence
from backend_scout.yaml_files import load_yaml_mapping

DEFAULT_CAREER_EVIDENCE_PATH = Path("config/career_evidence.yaml")


def load_career_evidence(path: Path = DEFAULT_CAREER_EVIDENCE_PATH) -> CareerEvidence:
    """Load the private, factual source material permitted in tailored CVs."""
    return CareerEvidence.model_validate(load_yaml_mapping(path))
