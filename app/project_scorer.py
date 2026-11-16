"""Validate project-scoring responses from an explicitly supplied client."""

import json
from typing import Callable, Dict, List, Tuple

from app.route import JobPosting


ProjectClient = Callable[[str, str], str]
DIMENSIONS = ("relevance", "contribution", "result")
POINT_LEVELS = (0, 3, 7, 10)


class InvalidProjectResponse(ValueError):
    """The client response does not match the project scoring contract."""


def _unique_object(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidProjectResponse("项目分回包包含重复字段")
        result[key] = value
    return result


def score_project(snippet: str, job: JobPosting, client: ProjectClient) -> int:
    """Score a supplied project excerpt using its text and the job requirements."""
    if not snippet.strip():
        return 0

    requirements = job.raw_text.strip() or "、".join(job.skills)
    response = client(snippet, requirements)
    if not isinstance(response, str) or not response.strip():
        raise InvalidProjectResponse("项目分回包为空或不是文本")
    try:
        payload = json.loads(response, object_pairs_hook=_unique_object)
    except json.JSONDecodeError:
        raise InvalidProjectResponse("项目分回包必须为 JSON 对象") from None
    if not isinstance(payload, dict) or payload.keys() != set(DIMENSIONS):
        raise InvalidProjectResponse("项目分回包必须恰好包含三个评分维度")

    total = 0
    for dimension in DIMENSIONS:
        entry = payload[dimension]
        if not isinstance(entry, dict) or entry.keys() != {"points", "evidence"}:
            raise InvalidProjectResponse(f"{dimension} 必须包含分值和原文引证")
        points, evidence = entry["points"], entry["evidence"]
        if type(points) is not int or points not in POINT_LEVELS:
            raise InvalidProjectResponse(f"{dimension} 分值不属于允许的整数档位")
        if not isinstance(evidence, str):
            raise InvalidProjectResponse(f"{dimension} 引证必须为字符串")
        quote = evidence.strip()
        if points == 0 and quote:
            raise InvalidProjectResponse(f"{dimension} 零分时引证必须为空")
        if points != 0 and (not quote or quote not in snippet):
            raise InvalidProjectResponse(f"{dimension} 非零分必须引用项目原文")
        total += points
    return total
