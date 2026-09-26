import json
import zipfile
from pathlib import Path

from app.services.files import extract_archive, safe_member_path, student_name
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
