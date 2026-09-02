from pathlib import Path

from backend_scout.models import CvStyle
from backend_scout.yaml_files import load_yaml_mapping

DEFAULT_CV_STYLE_PATH = Path("config/cv_style.yaml")


def load_cv_style(path: Path = DEFAULT_CV_STYLE_PATH) -> CvStyle:
    """Load the private presentation contract used for every CV draft."""
    return CvStyle.model_validate(load_yaml_mapping(path))
