import base64
import io
import mimetypes
import re
import zipfile
from pathlib import Path

from ..config import SUPPORTED_SUFFIXES


def safe_member_path(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("压缩包中包含不安全的路径")
    return path


def extract_archive(archive: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            if member.is_dir():
                continue
            relative = safe_member_path(member.filename)
            output = destination / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(zipped.read(member))
            if output.suffix.lower() in SUPPORTED_SUFFIXES:
                files.append(output)
    return files


def student_name(path: Path) -> str:
    cleaned = re.sub(r"[_\-\s]+", " ", path.stem).strip()
    return cleaned or "未命名学生"


def read_submission(path: Path) -> tuple[str, str | None]:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace"), None
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages), None
    if suffix == ".docx":
        from docx import Document

        document = Document(str(path))
        return "\n".join(paragraph.text for paragraph in document.paragraphs), None
    if suffix in {".jpg", ".jpeg", ".png"}:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return "", f"data:{mime};base64,{encoded}"
    raise ValueError(f"暂不支持的文件类型：{suffix}")


def archive_name(path: Path) -> str:
    return path.name
