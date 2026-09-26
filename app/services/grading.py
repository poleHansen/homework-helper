import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .. import db
from ..config import env_model_settings
from .files import extract_archive, read_submission, student_name, write_reviewed_docx


SYSTEM_PROMPT = """你是一名严谨的教师批改助手。
你必须严格按照评分标准批改，不得自行增加评分标准。
只输出合法 JSON，不要输出 Markdown，不要输出额外说明。
JSON 格式：
{
  "score": 数字,
  "max_score": 数字,
  "deductions": [
    {
      "question": "题号或位置",
      "points": 数字,
      "category": "错误分类",
      "reason": "不超过50字的扣分原因",
      "evidence": "学生答案中的证据"
    }
  ],
  "comments": ["给学生的简短改进建议"],
  "confidence": 0到1之间的数字,
  "needs_review": true或false
}"""


def parse_response(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("模型返回的结果不是 JSON 对象")
    return result


async def call_model(rubric: str, content: str, image_data_url: str | None) -> dict[str, Any]:
    settings = model_settings()
    if not settings["model"]:
        raise RuntimeError("尚未配置模型名称，请打开模型设置完成配置")

    from litellm import acompletion

    user_content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": f"评分标准：\n{rubric}\n\n学生作业内容：\n{content}",
        }
    ]
    if image_data_url:
        user_content.append({"type": "image_url", "image_url": {"url": image_data_url}})

    kwargs: dict[str, Any] = {
        "model": settings["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "api_key": settings["api_key"] or None,
    }
    if settings["api_base"]:
        kwargs["api_base"] = settings["api_base"]
    response = await acompletion(**kwargs, timeout=90)
    return parse_response(response.choices[0].message.content or "")


def model_settings() -> dict[str, str]:
    settings = env_model_settings()
    settings.update(db.get_settings(["model", "api_key", "api_base", "concurrency"]))
    return settings


def public_model_settings() -> dict[str, Any]:
    settings = model_settings()
    api_key = settings["api_key"]
    return {
        "model": settings["model"],
        "api_base": settings["api_base"],
        "concurrency": max(1, int(settings["concurrency"] or "3")),
        "api_key_configured": bool(api_key),
        "api_key_masked": f"{api_key[:4]}••••{api_key[-4:]}" if len(api_key) > 8 else ("已配置" if api_key else ""),
    }


def readable_error(error: Exception) -> str:
    message = str(error).strip()
    if not message:
        return error.__class__.__name__
    return message[:500]


async def test_model_connection() -> dict[str, Any]:
    settings = model_settings()
    if not settings["model"]:
        raise RuntimeError("尚未配置模型名称")

    from litellm import acompletion

    kwargs: dict[str, Any] = {
        "model": settings["model"],
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "api_key": settings["api_key"] or None,
        "max_tokens": 8,
        "timeout": 30,
    }
    if settings["api_base"]:
        kwargs["api_base"] = settings["api_base"]
    response = await acompletion(**kwargs)
    content = (response.choices[0].message.content or "").strip()
    return {"ok": True, "response": content, "model": settings["model"]}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    categories = Counter()
    reasons = Counter()
    for result in results:
        for deduction in result.get("deductions", []):
            category = deduction.get("category", "未分类错误")
            reason = deduction.get("reason", "未填写原因")
            categories[category] += 1
            reasons[reason] += 1
    return {
        "common_errors": [
            {"category": key, "count": count}
            for key, count in categories.most_common(10)
        ],
        "common_reasons": [
            {"reason": key, "count": count}
            for key, count in reasons.most_common(10)
        ],
    }


async def grade_submission(submission_id: int, rubric: str, content: str, image_data_url: str | None) -> None:
    with db.connect() as connection:
        connection.execute(
            "UPDATE submissions SET status = 'grading', updated_at = ? WHERE id = ?",
            (db.now(), submission_id),
        )
    try:
        result = await call_model(rubric, content, image_data_url)
        with db.connect() as connection:
            submission = connection.execute(
                "SELECT source_path FROM submissions WHERE id = ?", (submission_id,)
            ).fetchone()
        if not submission:
            raise RuntimeError("作业不存在")
        source_path = Path(submission["source_path"])
        if source_path.suffix.lower() == ".docx":
            await asyncio.to_thread(
                write_reviewed_docx,
                source_path,
                result.get("score"),
                result.get("max_score"),
                result.get("deductions", []),
                result.get("comments", []),
            )
        with db.connect() as connection:
            connection.execute(
                """
                UPDATE submissions
                SET status = 'completed', score = ?, max_score = ?,
                    result_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    result.get("score"),
                    result.get("max_score"),
                    json.dumps(result, ensure_ascii=False),
                    db.now(),
                    submission_id,
                ),
            )
    except Exception as error:
        with db.connect() as connection:
            connection.execute(
                "UPDATE submissions SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                (readable_error(error), db.now(), submission_id),
            )


def mark_batch_failed(batch_id: int, error: Exception | str) -> None:
    message = readable_error(error) if isinstance(error, Exception) else str(error)
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE submissions SET status = 'failed', error = ?, updated_at = ?
            WHERE batch_id = ? AND status NOT IN ('completed', 'reviewed', 'failed')
            """,
            (message, db.now(), batch_id),
        )
        connection.execute(
            """
            UPDATE batches
            SET status = 'failed', failed = total, summary_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps({"error": message}, ensure_ascii=False), db.now(), batch_id),
        )


async def prepare_batch(batch_id: int, archive_path: str, files_dir: str) -> None:
    try:
        with db.connect() as connection:
            connection.execute(
                "UPDATE batches SET status = 'extracting', updated_at = ? WHERE id = ?",
                (db.now(), batch_id),
            )
        files = await asyncio.to_thread(
            extract_archive,
            Path(archive_path),
            Path(files_dir),
        )
        if not files:
            raise ValueError("ZIP 中没有支持的作业文件")

        parsed = await asyncio.gather(
            *(asyncio.to_thread(read_submission, path) for path in files)
        )
        timestamp = db.now()
        with db.connect() as connection:
            connection.executemany(
                """
                INSERT INTO submissions
                (batch_id, filename, student_name, source_path, content, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                [
                    (
                        batch_id,
                        path.name,
                        student_name(path),
                        str(path),
                        content,
                        timestamp,
                        timestamp,
                    )
                    for path, (content, _) in zip(files, parsed)
                ],
            )
            connection.execute(
                "UPDATE batches SET status = 'queued', total = ?, updated_at = ? WHERE id = ?",
                (len(files), db.now(), batch_id),
            )
        await process_batch(batch_id)
    except Exception as error:
        mark_batch_failed(batch_id, error)


async def process_batch(batch_id: int) -> None:
    with db.connect() as connection:
        batch = connection.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        submissions = connection.execute(
            "SELECT * FROM submissions WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        ).fetchall()
    if not batch:
        return

    settings = model_settings()
    if not settings["model"]:
        mark_batch_failed(batch_id, "尚未配置模型名称，请打开模型设置完成配置")
        return

    with db.connect() as connection:
        connection.execute(
            "UPDATE batches SET status = 'grading', updated_at = ? WHERE id = ?",
            (db.now(), batch_id),
        )

    semaphore = asyncio.Semaphore(max(1, int(settings["concurrency"] or "3")))

    async def run(submission):
        async with semaphore:
            try:
                content = submission["content"]
                image_data_url = None
                if not content:
                    content, image_data_url = await asyncio.to_thread(
                        read_submission,
                        Path(submission["source_path"]),
                    )
                await grade_submission(
                    submission["id"],
                    batch["rubric_snapshot"],
                    content,
                    image_data_url,
                )
            except Exception as error:
                with db.connect() as connection:
                    connection.execute(
                        """
                        UPDATE submissions
                        SET status = 'failed', error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (readable_error(error), db.now(), submission["id"]),
                    )

    await asyncio.gather(*(run(item) for item in submissions))

    with db.connect() as connection:
        completed = connection.execute(
            "SELECT status, result_json FROM submissions WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
        results = [db.decode_json(item["result_json"], {}) for item in completed if item["status"] == "completed"]
        summary = summarize(results)
        failures = [
            {"filename": item["filename"], "error": item["error"]}
            for item in connection.execute(
                "SELECT filename, error FROM submissions WHERE batch_id = ? AND status = 'failed'",
                (batch_id,),
            ).fetchall()
        ]
        summary["failures"] = failures[:20]
        failed = sum(item["status"] == "failed" for item in completed)
        status = "completed" if not failed else "completed_with_errors"
        connection.execute(
            """
            UPDATE batches
            SET status = ?, completed = ?, failed = ?, summary_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                len(results),
                failed,
                json.dumps(summary, ensure_ascii=False),
                db.now(),
                batch_id,
            ),
        )
