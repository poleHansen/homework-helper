import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .. import db
from ..config import env_model_settings
from .files import extract_archive, read_submission, student_name, write_reviewed_docx


SYSTEM_PROMPT = """你是一名严谨的教师批改助手。
你必须严格按照评分标准批改，不得自行增加评分标准。
总分必须使用评分规则提供的总分，不要根据学生表现自行改变总分。
每个扣分项必须填写学生作业中的可直接匹配的连续短语作为 evidence，不得填写“缺少内容”“命名混乱”等不存在于原文的概括词。
question 必须填写题号或作业中实际存在的标题、段落位置。
category 用于概括错误类型，例如“概念理解”“算法实现”“代码规范”。
knowledge_point 必须填写具体的可教学知识点，例如“循环边界条件判断”“递归终止条件”“Python 模块导入语法”，不得只写“原理描述不准确”“命名混乱”等笼统表述。
teaching_focus 必须说明下节课应重点讲什么，结合该学生的实际错误给出可执行的讲解方向，不要只写“加强学习”。
JSON 字符串中的反斜杠必须写成双反斜杠，例如公式中的 \\theta 必须输出为 \\\\theta。
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
      "knowledge_point": "具体知识点",
      "reason": "不超过50字的扣分原因",
      "teaching_focus": "下节课重点讲解内容",
      "evidence": "学生答案中的证据"
    }
  ],
  "comments": ["给学生的简短改进建议"],
  "confidence": 0到1之间的数字,
  "needs_review": true或false
}"""


def extract_max_score(rubric: str) -> int | float:
    patterns = (
        r"(?:总分|满分)\s*(?:为|是|：|:)?\s*(\d+(?:\.\d+)?)\s*分?",
        r"(?:合计|共计)\s*(\d+(?:\.\d+)?)\s*分",
    )
    for pattern in patterns:
        match = re.search(pattern, rubric, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            return int(value) if value.is_integer() else value
    return 100


def parse_response(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(_repair_invalid_json_escapes(text))
    except json.JSONDecodeError as error:
        raise ValueError(f"模型返回的 JSON 无法解析：{error.msg}") from error
    if not isinstance(result, dict):
        raise ValueError("模型返回的结果不是 JSON 对象")
    return result


def _repair_invalid_json_escapes(text: str) -> str:
    simple_escapes = {'"', "\\", "/"}
    control_escapes = {"b", "f", "n", "r", "t"}
    repaired: list[str] = []
    in_string = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"' and (index == 0 or text[index - 1] != "\\"):
            in_string = not in_string
            repaired.append(character)
            index += 1
            continue
        if in_string and character == "\\":
            next_character = text[index + 1] if index + 1 < len(text) else ""
            following_character = text[index + 2] if index + 2 < len(text) else ""
            if next_character in simple_escapes:
                repaired.extend((character, next_character))
                index += 2
                continue
            if next_character in control_escapes:
                if "A" <= following_character <= "Z" or "a" <= following_character <= "z":
                    repaired.extend(("\\\\", next_character))
                else:
                    repaired.extend((character, next_character))
                index += 2
                continue
            if next_character == "u":
                unicode_digits = text[index + 2 : index + 6]
                if len(unicode_digits) == 4 and all(
                    character in "0123456789abcdefABCDEF" for character in unicode_digits
                ):
                    repaired.extend((character, next_character, unicode_digits))
                    index += 6
                    continue
            repaired.extend(("\\\\", next_character))
            index += 2
            continue
        repaired.append(character)
        index += 1
    return "".join(repaired)


def calculate_score(result: dict[str, Any], max_score: int | float | None = None) -> dict[str, Any]:
    max_score = max_score if max_score is not None else result.get("max_score")
    if max_score is None:
        raise ValueError("评分规则缺少总分")
    try:
        max_value = float(max_score)
        deduction_total = sum(
            max(0.0, float(deduction.get("points") or 0))
            for deduction in result.get("deductions", [])
        )
    except (TypeError, ValueError) as error:
        raise ValueError("模型结果中的总分或扣分不是数字") from error

    score = max(0.0, min(max_value, max_value - deduction_total))
    result["max_score"] = int(max_value) if max_value.is_integer() else max_value
    result["score"] = int(score) if score.is_integer() else score
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
    reasons = Counter()
    error_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        for deduction in result.get("deductions", []):
            category = deduction.get("category", "未分类错误")
            reason = deduction.get("reason", "未填写原因")
            knowledge_point = deduction.get("knowledge_point") or reason or category
            teaching_focus = deduction.get("teaching_focus") or f"结合错误题目讲解“{knowledge_point}”并进行针对性练习"
            reasons[reason] += 1
            key = (category, knowledge_point)
            group = error_groups.setdefault(
                key,
                {
                    "category": category,
                    "knowledge_point": knowledge_point,
                    "count": 0,
                    "teaching_focus": teaching_focus,
                    "examples": [],
                },
            )
            group["count"] += 1
            if len(group["examples"]) < 3:
                group["examples"].append(
                    {
                        "question": deduction.get("question", ""),
                        "reason": reason,
                        "evidence": deduction.get("evidence", ""),
                    }
                )
    return {
        "common_errors": [
            group
            for group in sorted(
                error_groups.values(),
                key=lambda item: (-item["count"], item["category"], item["knowledge_point"]),
            )[:10]
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
        result = calculate_score(
            await call_model(rubric, content, image_data_url),
            extract_max_score(rubric),
        )
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
