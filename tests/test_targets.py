from pathlib import Path

import pytest

from backend_scout.targets import AtsProvider, load_target_companies


def test_target_company_yaml_validates_public_provider_and_board_token(tmp_path: Path) -> None:
    path = tmp_path / "targets.yaml"
    path.write_text(
        """
companies:
  - name: Israel Example
    provider: greenhouse
    board_token: israel-example
    enabled: true
""".strip(),
        encoding="utf-8",
    )

    targets = load_target_companies(path)

    assert targets.companies[0].provider == AtsProvider.GREENHOUSE


def test_target_company_yaml_rejects_unknown_provider(tmp_path: Path) -> None:
    path = tmp_path / "targets.yaml"
    path.write_text(
        "companies:\n  - name: Example\n    provider: linkedin\n    board_token: example",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="provider"):
        load_target_companies(path)
