"""PDF text extraction and basic text normalization."""

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class ExtractResult:
    """Result returned by the PDF extraction boundary."""

    text: str
    status: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def clean_text(text: str) -> str:
    """Normalize whitespace while preserving line boundaries."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def extract_pdf(pdf_path: str | Path) -> ExtractResult:
    """Extract text from a text-based PDF.

    The dependency is imported at the boundary so the rest of the application
    can still be imported when the optional PDF dependency is unavailable.
    """
    path = Path(pdf_path)
    if not path.exists():
        return ExtractResult("", "ERROR", f"文件不存在：{path}")

    try:
        import fitz
    except ImportError as exc:
        return ExtractResult("", "DEPENDENCY_MISSING", str(exc))

    try:
        with fitz.open(path) as document:
            text = "\n".join(page.get_text("text") or "" for page in document)
    except Exception as exc:  # PDF libraries expose several format-specific errors.
        return ExtractResult("", "ERROR", f"{type(exc).__name__}: {exc}")

    cleaned = clean_text(text)
    if not cleaned:
        return ExtractResult("", "EMPTY", "PDF 没有可提取的文字层")
    return ExtractResult(cleaned, "OK")


def guess_name(text: str, file_name: str = "") -> str:
    """Guess a Chinese candidate name from the first lines or file name."""
    for line in text.splitlines()[:4]:
        match = re.match(r"^([\u4e00-\u9fff]{2,4})(?:[，,、\s]|$)", line.strip())
        if match:
            return match.group(1)

    stem = Path(file_name).stem
    tail = re.split(r"[_\- ]+", stem)[-1]
    match = re.match(r"^[\u4e00-\u9fff]{2,4}", tail)
    return match.group(0) if match else ""
