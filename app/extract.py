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
    edu_score: int = 0
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

    title_words = {
        "个人信息", "基本信息", "求职意向", "工作经历", "教育背景", "教育经历",
        "项目经历", "专业技能", "自我评价", "后端开发", "前端开发",
        "本科", "硕士", "研究生", "博士", "大专", "专科", "高中", "中专",
    }
    non_empty_lines = [line.strip() for line in content.splitlines() if line.strip()]
    for line in non_empty_lines[:3]:
        first_line_match = re.match(
            r"^([\u4e00-\u9fff]{2,4})(?=\s|[，,、|｜·]|$)",
            line,
        )
        if first_line_match and first_line_match.group(1) not in title_words:
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

WORK_HEADS = ("工作经历", "实习经历", "工作经验", "职业经历")
SECTION_ENDS = (
    "项目经历", "项目经验", "专业技能", "技能", "教育背景", "教育经历",
    "自我评价", "获奖情况", "证书",
)
EDUCATION_LINE_WORDS = ("大学", "学院", "本科", "硕士", "博士", "教育", "学历", "GPA", "在读")
WORK_CONTEXT_WORDS = ("工作", "实习", "经验", "开发", "从业", "后端", "前端")
EDUCATION_CONTEXT_WORDS = ("本科", "大学", "学院", "学制", "在读")
NEGATION_WORDS = ("不", "没", "未", "无", "不会")
NEGATION_SPLIT_CHARS = "。！？；;!?\n，,"

_DATE_RANGE = re.compile(
    r"(20\d{2})(?:[./年-](\d{1,2})月?)?\s*"
    r"(?:[-–—~至到]|至)\s*"
    r"(?:(20\d{2})(?:[./年-](\d{1,2})月?)?|至今|今|现在|当前)"
)


def extract_years(text: str) -> float:
    """Extract explicit experience years, then union work date ranges."""
    content = text or ""
    explicit = []
    explicit_pattern = re.compile(
        r"(?<!\d)(\d{1,2}(?:\.\d+)?)\s*(?:年|years?|yrs?)(?!制|生)",
        re.I,
    )
    for match in explicit_pattern.finditer(content):
        value = float(match.group(1))
        sentence_start = max(
            (content.rfind(char, 0, match.start()) for char in NEGATION_SPLIT_CHARS),
            default=-1,
        ) + 1
        sentence_end_candidates = [content.find(char, match.end()) for char in NEGATION_SPLIT_CHARS]
        sentence_end_candidates = [position for position in sentence_end_candidates if position >= 0]
        sentence_end = min(sentence_end_candidates, default=len(content))
        context = content[sentence_start:sentence_end]
        has_work_context = any(word in context for word in WORK_CONTEXT_WORDS)
        has_education_context = any(word in context for word in EDUCATION_CONTEXT_WORDS)
        if 0 < value <= 40 and (has_work_context or not has_education_context):
            explicit.append(value)
    if explicit:
        return max(explicit)

    work_text = _work_section(content)
    today = date.today()
    spans = []
    for match in _DATE_RANGE.finditer(work_text):
        start_year = int(match.group(1))
        start_month = int(match.group(2) or 1)
        end_year = int(match.group(3) or today.year)
        end_month = int(match.group(4) or (today.month if match.group(3) is None else 12))
        if not 1 <= start_month <= 12 or not 1 <= end_month <= 12:
            continue
        start = start_year * 12 + start_month - 1
        end = end_year * 12 + end_month - 1
        if end > start:
            spans.append((start, end))

    if not spans:
        return 0.0

    total_months = 0
    cursor = None
    for start, end in sorted(spans):
        if cursor is None:
            total_months += end - start
            cursor = end
        elif start <= cursor:
            if end > cursor:
                total_months += end - cursor
                cursor = end
        else:
            total_months += end - start
            cursor = end
    return round(total_months / 12, 1)


def extract_education(text: str) -> tuple[str, int]:
    """Return the first matched education level and its configured score."""
    content = text or ""
    for level, score in EDUCATION_LEVELS:
        if level in content:
            return level, score
    return "未知", 0


def extract_skills(text: str) -> List[str]:
    """Return vocabulary matches once each, preserving vocabulary order."""
    content = (text or "").lower()
    return [skill for skill in SKILL_VOCABULARY if _skill_present(content, skill)]


def _skill_present(content: str, skill: str) -> bool:
    """Match skills with token boundaries and sentence-level negation."""
    if any("\u4e00" <= char <= "\u9fff" for char in skill):
        pattern = re.escape(skill.lower())
    else:
        pattern = rf"(?<![a-z0-9_]){re.escape(skill.lower())}(?![a-z0-9_])"

    for match in re.finditer(pattern, content, re.IGNORECASE):
        clause_start = max(
            (content.rfind(char, 0, match.start()) for char in NEGATION_SPLIT_CHARS),
            default=-1,
        ) + 1
        before_skill = content[clause_start:match.start()]
        if any(word in before_skill for word in NEGATION_WORDS):
            continue
        return True
    return False


def _work_section(text: str) -> str:
    """Return work and internship sections while excluding education dates."""
    lines = text.splitlines()
    start = None
    inline_content = []
    for index, line in enumerate(lines):
        matched_head = next((head for head in WORK_HEADS if head in line), None)
        if matched_head:
            start = index + 1
            remainder = line.split(matched_head, 1)[1].lstrip("：: \t")
            if remainder:
                inline_content.append(remainder)
            break

    if start is None:
        return "\n".join(
            line for line in lines
            if not any(word in line for word in EDUCATION_LINE_WORDS)
        )

    end = len(lines)
    for index in range(start, len(lines)):
        if any(section in lines[index] for section in SECTION_ENDS):
            end = index
            break
    return "\n".join(inline_content + lines[start:end])


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
