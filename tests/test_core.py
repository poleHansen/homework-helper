import json
import zipfile
from pathlib import Path

from docx import Document

from app.services.files import extract_archive, safe_member_path, student_name
from app.services.files import REVIEW_MARKER, write_reviewed_docx
from app.services.grading import parse_response, summarize
from app import db


def test_safe_archive_and_student_name(tmp_path: Path):
    archive = tmp_path / "homework.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("张三_001.txt", "答案")
    output = tmp_path / "files"
    files = extract_archive(archive, output)
    assert files[0].read_text(encoding="utf-8") == "答案"
    assert student_name(files[0]) == "张三 001"


def test_rejects_traversal():
    try:
        safe_member_path("../outside.txt")
    except ValueError:
        pass
    else:
        raise AssertionError("必须拒绝目录穿越路径")


def test_parse_and_summarize():
    result = parse_response(json.dumps({
        "score": 8,
        "max_score": 10,
        "deductions": [{"category": "概念遗漏", "reason": "缺少定义域"}],
    }))
    summary = summarize([result, result])
    assert result["score"] == 8
    assert summary["common_errors"][0] == {"category": "概念遗漏", "count": 2}


def test_settings_round_trip(tmp_path, monkeypatch):
    database = tmp_path / "settings.db"
    monkeypatch.setattr(db, "DATABASE_PATH", database)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    db.set_settings({"model": "test/model", "concurrency": "4"})
    assert db.get_settings(["model", "concurrency"]) == {
        "model": "test/model",
        "concurrency": "4",
    }


def test_write_reviewed_docx_replaces_previous_review(tmp_path: Path):
    path = tmp_path / "student.docx"
    document = Document()
    document.add_paragraph("学生原文答案")
    document.save(path)

    write_reviewed_docx(
        path,
        8,
        10,
        [{"question": "第1题", "points": 2, "reason": "概念遗漏", "evidence": "未写定义"}],
        ["请补充关键定义"],
    )
    write_reviewed_docx(
        path,
        9,
        10,
        [{"question": "第2题", "points": 1, "reason": "计算错误"}],
    )

    result = Document(path)
    paragraphs = result.paragraphs
    assert paragraphs[0].text == "学生原文答案"
    assert sum(item.text == REVIEW_MARKER for item in paragraphs) == 1
    assert "最终得分：9 / 10" in [item.text for item in paragraphs]
    assert "第2题：扣分 1 分：计算错误" in [item.text for item in paragraphs]
    assert "第1题" not in "\n".join(item.text for item in paragraphs)
    assert paragraphs[-1].runs[0].font.color.rgb is not None
