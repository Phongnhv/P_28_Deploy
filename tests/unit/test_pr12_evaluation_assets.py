from pathlib import Path


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def test_pr12_evaluation_report_exists_and_valid():
    """Kiểm tra sự tồn tại và tính đầy đủ của báo cáo 5 kịch bản E1-E5."""
    root = _get_project_root()
    eval_file = root / "eval" / "results" / "E1_E5_EVALUATION.md"
    master_report = root / "eval" / "results" / "report.md"

    assert eval_file.exists(), "Tệp eval/results/E1_E5_EVALUATION.md không tồn tại."
    assert master_report.exists(), "Tệp eval/results/report.md không tồn tại."

    content = eval_file.read_text(encoding="utf-8")
    assert "numeric_range" in content
    assert "not_null" in content
    assert "accepted_values" in content
    assert "cross_field_comparison" in content
    assert "duplicate_fingerprint" in content
    assert "E1" in content
    assert "E5" in content


def test_pr12_architecture_diagram_sync():
    """Kiểm tra ARCHITECTURE.md chứa sơ đồ Mermaid Gate 2 MVP."""
    root = _get_project_root()
    arch_file = root / "ARCHITECTURE.md"

    assert arch_file.exists(), "Tệp ARCHITECTURE.md không tồn tại."

    content = arch_file.read_text(encoding="utf-8")
    assert "flowchart TB" in content or "graph" in content
    assert "Data Steward Browser" in content or "Vercel" in content
    assert "Google Cloud Run" in content
    assert "Supabase PostgreSQL" in content


def test_pr12_video_rehearsal_script_checkpoints():
    """Kiểm tra tệp presentation/VIDEO_REHEARSAL.md chứa đủ 6 mốc thời gian."""
    root = _get_project_root()
    video_file = root / "presentation" / "VIDEO_REHEARSAL.md"

    assert video_file.exists(), "Tệp presentation/VIDEO_REHEARSAL.md không tồn tại."

    content = video_file.read_text(encoding="utf-8")
    assert "0:00 – 0:20" in content or "0:00–0:20" in content
    assert "0:20 – 0:45" in content or "0:20–0:45" in content
    assert "0:45 – 1:20" in content or "0:45–1:20" in content
    assert "1:20 – 1:55" in content or "1:20–1:55" in content
    assert "1:55 – 2:30" in content or "1:55–2:30" in content
    assert "2:30 – 3:00" in content or "2:30–3:00" in content
