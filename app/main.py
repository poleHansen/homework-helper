import asyncio
import io
import json
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import db
from .config import DATA_DIR, UPLOAD_DIR
from .services.files import archive_name, read_rubric, write_reviewed_docx
from .services.grading import (
    mark_batch_failed,
    model_settings,
    prepare_batch,
    public_model_settings,
    test_model_connection,
)


app = FastAPI(title="作业批改工作台")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/assets", StaticFiles(directory="static/dist/assets"), name="assets")
templates = Jinja2Templates(directory="templates")
running_tasks: dict[int, asyncio.Task] = {}


class ReviewUpdate(BaseModel):
    score: float | None = None
    teacher_comment: str = ""
    deductions: list[dict] | None = None


class ModelSettingsUpdate(BaseModel):
    model: str
    api_base: str = ""
    api_key: str | None = None
    concurrency: int = 3


@app.on_event("startup")
def startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    frontend = Path("static/dist/index.html")
    if frontend.exists():
        return HTMLResponse(frontend.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>前端尚未构建</h1><p>请先运行 <code>npm install</code> 和 <code>npm run build</code>。</p>",
        status_code=503,
    )


@app.get("/api/rubrics")
def list_rubrics():
    with db.connect() as connection:
        rows = connection.execute("SELECT * FROM rubrics ORDER BY updated_at DESC").fetchall()
    return [db.row_to_dict(row) for row in rows]


@app.get("/api/rubrics/{rubric_id}/file")
def rubric_file(rubric_id: int):
    with db.connect() as connection:
        row = connection.execute(
            "SELECT source_path FROM rubrics WHERE id = ?",
            (rubric_id,),
        ).fetchone()
    if not row or not row["source_path"] or not Path(row["source_path"]).exists():
        raise HTTPException(status_code=404, detail="评分标准原文件不存在")
    return FileResponse(row["source_path"])


@app.get("/api/settings/model")
def get_model_settings():
    return public_model_settings()


@app.put("/api/settings/model")
def update_model_settings(payload: ModelSettingsUpdate):
    if not payload.model.strip():
        raise HTTPException(status_code=400, detail="模型名称不能为空")
    if payload.concurrency < 1 or payload.concurrency > 20:
        raise HTTPException(status_code=400, detail="并发数必须在 1 到 20 之间")

    values = {
        "model": payload.model.strip(),
        "api_base": payload.api_base.strip(),
        "concurrency": str(payload.concurrency),
    }
    if payload.api_key is not None and payload.api_key.strip():
        values["api_key"] = payload.api_key.strip()
    db.set_settings(values)
    return public_model_settings()


@app.post("/api/settings/model/test")
async def test_model_settings():
    try:
        return await test_model_connection()
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)[:500]) from error


@app.post("/api/rubrics")
async def create_rubric(
    name: str = Form(...),
    description: str = Form(""),
    rule_file: UploadFile = File(...),
):
    if not name.strip() or not rule_file.filename:
        raise HTTPException(status_code=400, detail="评分标准名称和内容不能为空")
    suffix = Path(rule_file.filename).suffix.lower()
    if suffix not in {".docx", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="评分规则只支持 DOCX、TXT 或 Markdown 文件")
    with tempfile.NamedTemporaryFile(dir=DATA_DIR, suffix=suffix, delete=False) as temporary:
        rubric_path = Path(temporary.name)
    try:
        rubric_path.write_bytes(await rule_file.read())
        content = read_rubric(rubric_path)
        if not content:
            raise HTTPException(status_code=400, detail="评分规则文件不能为空")
        timestamp = db.now()
        with db.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO rubrics
                (name, description, content, source_path, created_at, updated_at)
                VALUES (?, ?, ?, '', ?, ?)
                """,
                (name.strip(), description.strip(), content, timestamp, timestamp),
            )
            rubric_id = cursor.lastrowid
            rubric_dir = DATA_DIR / "rubrics"
            rubric_dir.mkdir(parents=True, exist_ok=True)
            source_path = rubric_dir / f"{rubric_id}{suffix}"
            rubric_path.replace(source_path)
            connection.execute(
                "UPDATE rubrics SET source_path = ? WHERE id = ?",
                (str(source_path), rubric_id),
            )
            row = connection.execute("SELECT * FROM rubrics WHERE id = ?", (rubric_id,)).fetchone()
        return db.row_to_dict(row)
    finally:
        rubric_path.unlink(missing_ok=True)


@app.get("/api/batches")
def list_batches():
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT batches.*, rubrics.name AS rubric_name
            FROM batches JOIN rubrics ON rubrics.id = batches.rubric_id
            ORDER BY batches.created_at DESC
            """
        ).fetchall()
    return [db.row_to_dict(row) for row in rows]


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: int):
    with db.connect() as connection:
        batch = connection.execute(
            """
            SELECT batches.*, rubrics.name AS rubric_name
            FROM batches JOIN rubrics ON rubrics.id = batches.rubric_id
            WHERE batches.id = ?
            """,
            (batch_id,),
        ).fetchone()
        submissions = connection.execute(
            "SELECT * FROM submissions WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        ).fetchall()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    result = db.row_to_dict(batch)
    result["summary"] = db.decode_json(result.pop("summary_json"), {})
    result["submissions"] = []
    for item in submissions:
        submission = db.row_to_dict(item)
        submission["result"] = db.decode_json(submission.pop("result_json"), {})
        result["submissions"].append(submission)
    return result


@app.post("/api/batches")
async def create_batch(
    name: str = Form(...),
    rubric_id: int = Form(...),
    archive: UploadFile = File(...),
):
    with db.connect() as connection:
        rubric = connection.execute("SELECT * FROM rubrics WHERE id = ?", (rubric_id,)).fetchone()
    if not rubric:
        raise HTTPException(status_code=400, detail="评分标准不存在")
    if not archive.filename or not archive.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 ZIP 文件")

    timestamp = db.now()
    with db.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO batches
            (name, rubric_id, rubric_snapshot, status, created_at, updated_at)
            VALUES (?, ?, ?, 'extracting', ?, ?)
            """,
            (name.strip() or archive.filename, rubric_id, rubric["content"], timestamp, timestamp),
        )
        batch_id = cursor.lastrowid

    batch_dir = UPLOAD_DIR / str(batch_id)
    archive_path = batch_dir / archive_name(Path(archive.filename))
    batch_dir.mkdir(parents=True, exist_ok=True)
    try:
        archive_path.write_bytes(await archive.read())
    except Exception as error:
        mark_batch_failed(batch_id, f"保存上传文件失败：{error}")
        raise HTTPException(status_code=500, detail="保存上传文件失败") from error

    task = asyncio.create_task(
        prepare_batch(
            batch_id,
            str(archive_path),
            str(batch_dir / "files"),
        )
    )
    running_tasks[batch_id] = task
    task.add_done_callback(lambda completed: running_tasks.pop(batch_id, None))
    return {"id": batch_id, "status": "extracting"}


@app.delete("/api/batches/{batch_id}")
async def delete_batch(batch_id: int):
    with db.connect() as connection:
        batch = connection.execute("SELECT id FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")

    task = running_tasks.get(batch_id)
    if task:
        await asyncio.gather(task, return_exceptions=True)

    batch_dir = UPLOAD_DIR / str(batch_id)
    if batch_dir.exists():
        try:
            await asyncio.to_thread(shutil.rmtree, batch_dir)
        except OSError as error:
            raise HTTPException(status_code=500, detail=f"删除批次文件失败：{error}") from error

    with db.connect() as connection:
        connection.execute("DELETE FROM submissions WHERE batch_id = ?", (batch_id,))
        connection.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    return {"status": "deleted"}


@app.post("/api/submissions/{submission_id}/review")
def review_submission(submission_id: int, payload: ReviewUpdate):
    with db.connect() as connection:
        row = connection.execute(
            "SELECT result_json, source_path, score, max_score FROM submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="作业不存在")
        if Path(row["source_path"]).suffix.lower() != ".docx":
            raise HTTPException(status_code=400, detail="当前只支持复核 Word 作业")
        result = db.decode_json(row["result_json"], {})
        score = payload.score if payload.score is not None else row["score"]
        deductions = payload.deductions if payload.deductions is not None else result.get("deductions", [])
        previous_teacher_comment = result.get("teacher_comment", "")
        result["reviewed"] = True
        result["teacher_comment"] = payload.teacher_comment
        result["deductions"] = deductions
        comments = list(result.get("comments", []))
        if previous_teacher_comment:
            comments = [comment for comment in comments if comment != previous_teacher_comment]
        if payload.teacher_comment.strip():
            comments.append(payload.teacher_comment.strip())
        result["comments"] = comments
        try:
            write_reviewed_docx(
                Path(row["source_path"]),
                score,
                result.get("max_score", row["max_score"]),
                deductions,
                comments,
            )
        except Exception as error:
            raise HTTPException(status_code=500, detail=f"写回 Word 失败：{error}") from error
        connection.execute(
            """
            UPDATE submissions SET status = 'reviewed', score = ?,
            result_json = ?, updated_at = ? WHERE id = ?
            """,
            (score, json.dumps(result, ensure_ascii=False), db.now(), submission_id),
        )
    return {"status": "reviewed", "score": score, "result": result}


@app.get("/api/submissions/{submission_id}/file")
def submission_file(submission_id: int):
    with db.connect() as connection:
        row = connection.execute(
            "SELECT source_path, filename, status FROM submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
    if not row or row["status"] not in {"completed", "reviewed"} or not Path(row["source_path"]).exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(row["source_path"], filename=row["filename"])


@app.get("/api/submissions/{submission_id}/preview")
def submission_preview(submission_id: int):
    with db.connect() as connection:
        row = connection.execute(
            "SELECT source_path, filename FROM submissions WHERE id = ?",
            (submission_id,),
        ).fetchone()
    if not row or not Path(row["source_path"]).exists():
        raise HTTPException(status_code=404, detail="作业文件不存在")
    return FileResponse(row["source_path"], filename=row["filename"])


@app.get("/api/batches/{batch_id}/download")
def batch_download(batch_id: int):
    with db.connect() as connection:
        batch = connection.execute("SELECT name FROM batches WHERE id = ?", (batch_id,)).fetchone()
        submissions = connection.execute(
            """
            SELECT filename, student_name, source_path, status
            FROM submissions
            WHERE batch_id = ? AND status IN ('completed', 'reviewed')
              AND lower(filename) LIKE '%.docx'
            ORDER BY id
            """,
            (batch_id,),
        ).fetchall()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    files = [item for item in submissions if Path(item["source_path"]).exists()]
    if not files:
        raise HTTPException(status_code=400, detail="暂无可下载的已批改作业")

    archive = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for item in files:
            filename = f"{item['student_name']}.docx"
            if filename in used_names:
                filename = item["filename"]
            used_names.add(filename)
            zipped.write(item["source_path"], filename)
    archive.seek(0)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                "attachment; "
                f"filename*=UTF-8''{quote(archive_name(Path(batch['name'] + '.zip')))}"
            )
        },
    )
