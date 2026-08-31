from pathlib import Path
from typing import Any


class YamlFileError(ValueError):
    """Raised when a YAML file cannot be loaded into the expected shape."""


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise YamlFileError("Missing dependency: PyYAML. Run: uv sync --extra dev") from exc

    if not path.exists():
        raise YamlFileError(f"File does not exist: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise YamlFileError(f"Invalid YAML in {path}: {exc}") from exc

    if data is None:
        raise YamlFileError(f"YAML file is empty: {path}")

    if not isinstance(data, dict):
        raise YamlFileError(f"Expected a YAML mapping at the root of {path}")

    return data
