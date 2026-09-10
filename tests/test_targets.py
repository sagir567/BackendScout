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


def test_lin_srael_target_normalizes_search_terms(tmp_path: Path) -> None:
    path = tmp_path / "targets.yaml"
    path.write_text(
        """
companies:
  - name: Lin-Srael
    provider: lin_srael
    board_token: app-id
    search_terms: [Backend, " Backend ", Python]
    result_limit: 25
""".strip(),
        encoding="utf-8",
    )

    target = load_target_companies(path).companies[0]

    assert target.provider == AtsProvider.LIN_SRAEL
    assert target.search_terms == ["Backend", "Python"]
    assert target.result_limit == 25


def test_israel_starter_target_company_example_validates() -> None:
    targets = load_target_companies(Path("examples/target_companies.israel.example.yaml"))

    enabled = [target for target in targets.companies if target.enabled]
    assert len(enabled) >= 7
    assert {target.provider for target in enabled} >= {
        AtsProvider.GREENHOUSE,
        AtsProvider.LEVER,
        AtsProvider.ASHBY,
        AtsProvider.LIN_SRAEL,
    }
