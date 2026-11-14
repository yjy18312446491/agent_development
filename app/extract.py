"""PDF text extraction, normalization, and rule-based resume profiling."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import re
from typing import List, Union


@dataclass(frozen=True)
class ExtractResult:
    """Result returned by the PDF extraction boundary."""

    text: str
    status: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "OK"


@dataclass
class CandidateProfile:
    """Structured resume fields used by routing and scoring."""

    name: str = ""
    years: float = 0.0
    education: str = "未知"
    edu_score: int = 4
    skills: List[str] = field(default_factory=list)


def clean_text(text: str) -> str:
    """Normalize whitespace while preserving line boundaries."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def extract_pdf(pdf_path: Union[str, Path]) -> ExtractResult:
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
    """Guess a Chinese candidate name from text and then the file name."""
    content = text or ""

    named = re.search(
        r"(?:姓\s*名|名字)\s*[：:]\s*([\u4e00-\u9fff]{2,10})",
        content[:400],
    )
    if named:
        return named.group(1)

    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    first_line_match = re.match(
        r"^([\u4e00-\u9fff]{2,4})(?=\s*(?:[，,、|｜·]|$))",
        first_line,
    )
    if first_line_match:
        return first_line_match.group(1)

    stem = Path(file_name).stem
    ignored = {"简历", "个人简历", "resume", "cv"}
    parts = re.split(r"[_\-\s]+", stem)
    for part in reversed(parts):
        if part.lower() in ignored:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", part):
            return part
    return ""


EDUCATION_LEVELS = (
    ("博士", 10),
    ("硕士", 9),
    ("研究生", 9),
    ("本科", 7),
    ("学士", 7),
    ("大专", 5),
    ("专科", 5),
    ("高中", 3),
    ("中专", 3),
)

SKILL_VOCABULARY = (
    "Python", "Java", "Go", "C++", "PHP", "JavaScript", "TypeScript",
    "Flask", "Django", "FastAPI", "Gin", "Spring", "Vue", "React",
    "MySQL", "PostgreSQL", "Redis", "Kafka", "RabbitMQ", "Celery",
    "Docker", "Kubernetes", "K8s", "Nginx", "Linux", "Git", "CI/CD",
    "Prometheus", "Grafana", "微服务", "分布式", "RAG", "LangChain",
)


def extract_years(text: str) -> float:
    """Extract explicit experience years, then fall back to date ranges."""
    content = text or ""
    explicit = []
    for match in re.finditer(r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*(?:年|years?|yrs?)", content, re.I):
        value = float(match.group(1))
        if 0 < value <= 40:
            explicit.append(value)
    if explicit:
        return max(explicit)

    date_range = re.compile(
        r"(20\d{2})(?:[./年-](\d{1,2})月?)?\s*"
        r"(?:[-—~至到]|至)\s*"
        r"(?:(20\d{2})(?:[./年-](\d{1,2})月?)?|至今|今|现在|当前)"
    )
    today = date.today()
    spans = []
    for match in date_range.finditer(content):
        start_year = int(match.group(1))
        start_month = int(match.group(2) or 1)
        end_year = int(match.group(3) or today.year)
        end_month = int(match.group(4) or (today.month if match.group(3) is None else 12))
        if not 1 <= start_month <= 12 or not 1 <= end_month <= 12:
            continue
        span = (end_year - start_year) + (end_month - start_month) / 12
        if 0 < span <= 40:
            spans.append(round(span, 1))
    return max(spans) if spans else 0.0


def extract_education(text: str) -> tuple[str, int]:
    """Return the first matched education level and its configured score."""
    content = text or ""
    for level, score in EDUCATION_LEVELS:
        if level in content:
            return level, score
    return "未知", 4


def extract_skills(text: str) -> List[str]:
    """Return vocabulary matches once each, preserving vocabulary order."""
    content = (text or "").lower()
    return [skill for skill in SKILL_VOCABULARY if _skill_present(content, skill)]


def _skill_present(content: str, skill: str) -> bool:
    """Match ASCII skill names as tokens and Chinese names as phrases."""
    if any("\u4e00" <= char <= "\u9fff" for char in skill):
        return skill.lower() in content

    pattern = rf"(?<![a-z0-9_]){re.escape(skill.lower())}(?![a-z0-9_])"
    return re.search(pattern, content, re.IGNORECASE) is not None


def extract_profile(text: str, file_name: str = "") -> CandidateProfile:
    """Build a deterministic candidate profile from extracted resume text."""
    education, edu_score = extract_education(text)
    return CandidateProfile(
        name=guess_name(text, file_name),
        years=extract_years(text),
        education=education,
        edu_score=edu_score,
        skills=extract_skills(text),
    )
