"""Exercise auxiliary retrieval with fixed vectors and no embedding service."""

from copy import deepcopy
from pathlib import Path

import pytest

from app.extract import extract_pdf, extract_profile
from app.retrieval import retrieve_job_evidence
from app.route import JobPosting, load_jobs, route_profile
from app.screen import screen_one


JOBS = (
    JobPosting("A", "API developer", 1, ["Python"], raw_text="Build APIs."),
    JobPosting("B", "UI developer", 1, ["React"], raw_text="Build pages."),
    JobPosting("C", "Data developer", 1, ["SQL"], raw_text="Build reports."),
)
PDF = Path(__file__).resolve().parents[1] / "data/synthetic/resumes/S001_normal_backend.pdf"


class FixedEmbedder:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def __call__(self, texts):
        self.calls.append(texts)
        return self.vectors


def test_default_disabled_never_calls_an_injected_embedder():
    def forbidden(texts):
        pytest.fail("Disabled retrieval must not call a provider")

    result = retrieve_job_evidence("candidate", JOBS, embedder=forbidden)
    assert result.status == "DISABLED" and result.hits == ()
    assert result.reason_code == "RETRIEVAL_DISABLED"


def test_ranks_by_cosine_with_original_job_evidence_and_one_batch_call():
    embedder = FixedEmbedder([[3, 4], [0, 2], [3, 4], [-1, 0]])
    original = deepcopy(JOBS)
    result = retrieve_job_evidence("candidate", JOBS, enabled=True, embedder=embedder, top_k=2)
    assert result.status == "OK"
    assert [hit.reference_jd_id for hit in result.hits] == ["B", "A"]
    assert [hit.similarity for hit in result.hits] == pytest.approx([1, 0.8])
    assert result.hits[0].text == "Build pages."
    assert result.hits[0].title == "UI developer"
    assert embedder.calls == [("candidate", "Build APIs.", "Build pages.", "Build reports.")]
    assert JOBS == original


def test_ties_use_stable_ids_and_keep_negative_similarity():
    for jobs in (JOBS, tuple(reversed(JOBS))):
        by_text = {"candidate": [1, 0], "Build APIs.": [0, 1],
                   "Build pages.": [0, 2], "Build reports.": [-1, 0]}
        result = retrieve_job_evidence(
            "candidate", jobs, enabled=True, embedder=lambda texts: [by_text[text] for text in texts],
            top_k=10,
        )
        assert [hit.reference_jd_id for hit in result.hits] == ["A", "B", "C"]
        assert result.hits[-1].similarity == -1


def test_missing_raw_text_uses_title_skills_and_years():
    job = JobPosting("X", "API developer", 1, ["Python"], max_years=3, bonus_skills=["Docker"])
    embedder = FixedEmbedder([[1], [1]])
    result = retrieve_job_evidence("candidate", [job], enabled=True, embedder=embedder)
    evidence = result.hits[0].text
    assert all(value in evidence for value in ("API developer", "Python", "Docker", "1", "3"))
    assert embedder.calls == [("candidate", evidence)]


@pytest.mark.parametrize("vectors", [
    None, [], [[1]], [[1], [1], [1], [1], [1]],
    [[1, 0], [1], [1, 0], [1, 0]],
    [[0, 0], [1, 0], [1, 0], [1, 0]],
    [[1, 0], [0, 0], [1, 0], [1, 0]],
    [[1], [float("nan")], [1], [1]],
    [[1], [float("inf")], [1], [1]],
    [[1], [True], [1], [1]], [[1], ["1"], [1], [1]],
    [[1], b"a", [1], [1]],
    [[1], [], [1], [1]], [[1], None, [1], [1]],
])
def test_invalid_vectors_return_review_without_partial_hits(vectors):
    result = retrieve_job_evidence("candidate", JOBS, enabled=True, embedder=FixedEmbedder(vectors))
    assert result.status == "REVIEW" and result.hits == ()
    assert result.reason_code == "EMBEDDING_INVALID"


@pytest.mark.parametrize("magnitude", [1e308, 1e-300])
def test_cosine_normalization_handles_finite_extreme_values(magnitude):
    result = retrieve_job_evidence(
        "candidate", JOBS[:1], enabled=True,
        embedder=FixedEmbedder([[magnitude, magnitude], [magnitude, magnitude]]),
    )
    assert result.status == "OK" and result.hits[0].similarity == pytest.approx(1)


@pytest.mark.parametrize("kwargs,code", [
    ({"text": " "}, "RETRIEVAL_INPUT_ERROR"),
    ({"text": None}, "RETRIEVAL_INPUT_ERROR"),
    ({"jobs": []}, "RETRIEVAL_INPUT_ERROR"),
    ({"jobs": [JOBS[0], JOBS[0]]}, "RETRIEVAL_INPUT_ERROR"),
    ({"jobs": [JobPosting("", "title", 0, [])]}, "RETRIEVAL_INPUT_ERROR"),
    ({"jobs": [None]}, "RETRIEVAL_INPUT_ERROR"),
    ({"top_k": 0}, "RETRIEVAL_INPUT_ERROR"),
    ({"top_k": True}, "RETRIEVAL_INPUT_ERROR"),
    ({"top_k": 1.5}, "RETRIEVAL_INPUT_ERROR"),
    ({"enabled": "true"}, "RETRIEVAL_INPUT_ERROR"),
])
def test_invalid_inputs_do_not_call_provider(kwargs, code):
    embedder = FixedEmbedder([])
    options = dict(text="candidate", jobs=JOBS, enabled=True, embedder=embedder)
    options.update(kwargs)
    result = retrieve_job_evidence(**options)
    assert result.status == "REVIEW" and result.hits == ()
    assert result.reason_code == code and embedder.calls == []


def test_explicit_enable_requires_an_injected_provider():
    result = retrieve_job_evidence("candidate", JOBS, enabled=True)
    assert result.status == "REVIEW" and result.reason_code == "EMBEDDER_MISSING"


def test_provider_exception_is_sanitized_and_not_retried():
    calls = []

    def fail(texts):
        calls.append(texts)
        raise RuntimeError("private provider details")

    result = retrieve_job_evidence("candidate", JOBS, enabled=True, embedder=fail)
    assert result.status == "REVIEW" and result.hits == ()
    assert result.reason_code == "EMBEDDING_ERROR" and "private" not in result.reason
    assert len(calls) == 1


@pytest.mark.parametrize("path,expected_id,expected_total", [
    (PDF, "JD1", 60),
    (PDF.parent / "live_pdf_eval/unmatched.pdf", "UNMATCHED", 0),
])
def test_auxiliary_rank_does_not_change_selected_or_unmatched_route_and_score(path, expected_id, expected_total):
    jobs = load_jobs()
    query = extract_pdf(path).text
    profile = extract_profile(query)
    route_before = route_profile(query, profile, jobs)
    screen_before = screen_one(path, jobs)
    # Deliberately prefer the frontend JD even for the backend PDF / UNMATCHED query.
    vectors = [[1, 0]] + [[1, 0] if job.jd_id == "JD3" else [0, 1] for job in jobs]
    evidence = retrieve_job_evidence(query, jobs, enabled=True, embedder=FixedEmbedder(vectors))
    assert evidence.hits[0].reference_jd_id == "JD3"
    assert route_before.jd_id == expected_id
    assert route_profile(query, profile, jobs) == route_before
    assert screen_one(path, jobs) == screen_before
    assert screen_before.score.jd_id == expected_id and screen_before.score.total == expected_total
    assert screen_before.score.scores.project == 0
