from pathlib import Path

import pytest

from backend_scout.cv_style import load_cv_style


def test_load_cv_style_accepts_a_private_style_contract(tmp_path: Path) -> None:
    path = tmp_path / "cv_style.yaml"
    path.write_text(
        """
include_headline: false
target_page_count: 1
show_raw_urls: false
writing_rules:
  - Use professional language.
""".strip(),
        encoding="utf-8",
    )

    style = load_cv_style(path)

    assert style.include_headline is False
    assert style.writing_rules == ["Use professional language."]


def test_load_cv_style_rejects_visible_raw_urls(tmp_path: Path) -> None:
    path = tmp_path / "cv_style.yaml"
    path.write_text("show_raw_urls: true", encoding="utf-8")

    with pytest.raises(ValueError, match="show_raw_urls"):
        load_cv_style(path)
