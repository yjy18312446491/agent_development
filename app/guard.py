"""Text quality gate between PDF extraction and downstream scoring."""

from dataclasses import dataclass
from typing import Optional

from app.extract import ExtractResult


CHAR_MIN = 200
HAN_MIN = 50
LETTER_MIN = 400
TEXT_RATIO_MIN = 0.5
TEXTUAL_MIN = 50


@dataclass(frozen=True)
class GuardResult:
    """Decision returned by the text quality gate."""

    passed: bool
    status: str
    reason_code: str
    reason: str
    score: Optional[int]
    text_len: int = 0
    han_count: int = 0
    letter_count: int = 0


def guard_text(extract: ExtractResult) -> GuardResult:
    """Classify extracted text as usable or requiring manual review.

    The gate only evaluates extraction quality. It does not inspect candidate
    fields, calculate a score, or decide a job direction.
    """
    if extract is None:
        return _review(
            "EXTRACT_ERROR",
            "人工复核：未收到 PDF 抽取结果",
            text_len=0,
        )

    if extract.status == "DEPENDENCY_MISSING":
        return _review(
            "DEPENDENCY_MISSING",
            "人工复核：缺少 PDF 解析依赖",
            text_len=len((extract.text or "").strip()),
        )

    if extract.status == "ERROR":
        detail = extract.error.strip() or "未知读取错误"
        return _review(
            "EXTRACT_ERROR",
            f"人工复核：文档读取失败，{detail}",
            text_len=len((extract.text or "").strip()),
        )

    text = (extract.text or "").strip()
    body = len(text)
    han_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
    letter_count = sum(char.isascii() and char.isalpha() for char in text)
    textual = han_count + letter_count
    ratio = textual / body if body else 0.0

    if extract.status == "EMPTY" or body == 0:
        return _review(
            "EMPTY",
            "人工复核：文件为空，无可提取内容",
            text_len=body,
            han_count=han_count,
            letter_count=letter_count,
        )

    if textual < TEXTUAL_MIN and ratio < TEXT_RATIO_MIN:
        return _review(
            "SUSPECT_SCAN_OR_CORRUPT",
            "人工复核：可能为图片PDF或简历损坏，请人工审核",
            text_len=body,
            han_count=han_count,
            letter_count=letter_count,
        )

    if body < CHAR_MIN:
        return _review(
            "TOO_SHORT",
            f"人工复核：内容过短，仅提取到 {body} 字（阈值 {CHAR_MIN} 字），请人工核实",
            text_len=body,
            han_count=han_count,
            letter_count=letter_count,
        )

    if han_count < HAN_MIN and letter_count < LETTER_MIN:
        return _review(
            "SUSPECT_SCAN_OR_CORRUPT",
            "人工复核：可能为图片PDF或简历损坏，请人工审核",
            text_len=body,
            han_count=han_count,
            letter_count=letter_count,
        )

    return GuardResult(
        passed=True,
        status="PASS",
        reason_code="",
        reason="",
        score=None,
        text_len=body,
        han_count=han_count,
        letter_count=letter_count,
    )


def _review(
    reason_code: str,
    reason: str,
    text_len: int,
    han_count: int = 0,
    letter_count: int = 0,
) -> GuardResult:
    return GuardResult(
        passed=False,
        status="REVIEW",
        reason_code=reason_code,
        reason=reason,
        score=-1,
        text_len=text_len,
        han_count=han_count,
        letter_count=letter_count,
    )
