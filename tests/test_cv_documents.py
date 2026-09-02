from pathlib import Path

from docx import Document

from backend_scout.cv_documents import create_cv_docx
from backend_scout.cv_tailoring import build_evidence_only_draft
from backend_scout.models import CareerEvidence, CvStyle, TailoredCv


def make_evidence() -> CareerEvidence:
    return CareerEvidence.model_validate(
        {
            "identity": {
                "full_name": "Test Candidate",
                "email": "candidate@example.com",
                "links": ["https://github.com/test-candidate"],
            },
            "summary": "Backend developer with Python experience.",
            "skills": [{"category": "Backend", "items": ["Python", "FastAPI"]}],
            "experience": [
                {
                    "id": "exp_1",
                    "organization": "Example Company",
                    "title": "Backend Developer",
                    "start_date": "2024-01",
                    "end_date": "Present",
                    "bullets": [
                        {
                            "id": "exp_1_bullet_1",
                            "text": "Built Python APIs for internal tools.",
                        }
                    ],
                }
            ],
        }
    )


def test_create_cv_docx_writes_a_readable_word_document(tmp_path: Path) -> None:
    evidence = make_evidence()
    output_path = tmp_path / "cv_draft.docx"

    create_cv_docx(evidence, build_evidence_only_draft(evidence), output_path)

    assert output_path.is_file()
    assert output_path.stat().st_size > 0


def test_create_cv_docx_uses_labeled_hyperlinks_and_hides_headline(tmp_path: Path) -> None:
    evidence = make_evidence()
    output_path = tmp_path / "cv_draft.docx"
    draft = TailoredCv(
        headline="Backend Engineer | Python",
        summary="Backend developer with Python experience.",
        summary_evidence_ids=["exp_1_bullet_1"],
    )

    create_cv_docx(evidence, draft, output_path, CvStyle())

    document = Document(output_path)
    visible_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    hyperlink_targets = [relationship.target_ref for relationship in document.part.rels.values()]
    assert "https://github.com/test-candidate" not in visible_text
    assert "Backend Engineer | Python" not in visible_text
    assert "https://github.com/test-candidate" in hyperlink_targets


def test_create_cv_docx_uses_per_link_label(tmp_path: Path) -> None:
    evidence = make_evidence()
    output_path = tmp_path / "cv_draft.docx"

    create_cv_docx(
        evidence,
        build_evidence_only_draft(evidence),
        output_path,
        CvStyle(link_labels={"https://github.com/test-candidate": "Personal GitHub"}),
    )

    document = Document(output_path)
    xml = document.part.element.xml
    assert "Personal GitHub" in xml
    assert "https://github.com/test-candidate" not in "\n".join(
        paragraph.text for paragraph in document.paragraphs
    )
