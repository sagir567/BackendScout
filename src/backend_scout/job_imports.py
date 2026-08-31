from pathlib import Path

from backend_scout.models import ManualJobImport
from backend_scout.yaml_files import load_yaml_mapping


def load_manual_job_import(path: Path) -> ManualJobImport:
    return ManualJobImport.model_validate(load_yaml_mapping(path))
