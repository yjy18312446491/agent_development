"""Offline relevance annotations and hand-computable fake metric contracts."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request

import pytest

from app.embedding_client import EmbeddingClient, EmbeddingConfig
from app.screen import screen_one
from eval import run_retrieval_eval as runner


@pytest.fixture(autouse=True)
def no_credentials_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline retrieval must not read credentials or make network requests")

    monkeypatch.setattr(EmbeddingConfig, "from_env_file", forbidden)
    monkeypatch.setattr(EmbeddingClient, "__init__", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    original_bytes, original_text = Path.read_bytes, Path.read_text

    def read_bytes(path, *args, **kwargs):
        if path.name == ".env":
            forbidden()
        return original_bytes(path, *args, **kwargs)

    def read_text(path, *args, **kwargs):
        if path.name == ".env":
            forbidden()
        return original_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "read_text", read_text)


def raw_dataset():
    return json.loads(runner.DATASET.read_text(encoding="utf-8"))


def save_dataset(tmp_path, raw):
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def test_twelve_independent_cases_have_thirty_six_explained_draft_grades():
    dataset = runner.load_dataset()
    assert len(dataset.cases) == 12
    assert [sum(c.coverage_group == group for c in dataset.cases) for group in (*runner.JOB_IDS, "none")] == [3] * 4
    assert sum(len(c.grades) for c in dataset.cases) == 36
    assert all(len(c.rationales) == 3 and all(c.rationales) for c in dataset.cases)
    assert len({c.query for c in dataset.cases}) == 12
    assert dataset.canonical_sha256 == runner.DATASET_SHA
    assert raw_dataset()["annotation_status"] == "draft_pending_cursor_review"
    assert all(c.case_id.startswith("RV") for c in dataset.cases)


def test_review_receipt_links_the_unchanged_candidate_to_corrected_independent_review():
    dataset = runner.load_dataset()
    report = runner.evaluate(dataset)
    assert dataset.canonical_sha256 == "704ff394272ce4b9b3b4cf25b59440d82ea44ffec3d738d965a918377cdfc04b"
    assert report["annotation_status"] == "draft_pending_cursor_review"  # Original immutable authoring metadata.
    assert report["annotation_review_status"] == "APPROVED"
    receipt = report["annotation_review"]
    assert receipt["reviewed_dataset_sha256"] == dataset.canonical_sha256
    assert receipt["record_sha256"] == runner.REVIEW_RECORD_SHA
    assert receipt["reviewed_head"] == "1fd133b1f1f64cc01239baf02c6f962b52c12e01"
    assert receipt["comment_url"].endswith("#issuecomment-7056976801")
    assert receipt["superseded_comment_url"].endswith("#issuecomment-7056814497")
    assert report["quality_conclusion"] == "NOT_ESTABLISHED" and report["real_http_requests"] == 0


@pytest.mark.parametrize("change", [
    lambda r: r.update(decision="REQUEST_CHANGES"),
    lambda r: r.update(reviewed_head="0" * 40),
    lambda r: r.update(dataset_sha256="0" * 64),
    lambda r: r.update(review_comment_url=r["superseded_comment_url"]),
    lambda r: r.update(reviewer="unknown"),
    lambda r: r.update(record_version=True),
    lambda r: r.pop("superseded_comment_url"),
    lambda r: r.update(extra="unexpected"),
])
def test_altered_review_receipts_are_rejected(tmp_path, change):
    receipt = json.loads(runner.REVIEW_RECORD.read_text(encoding="utf-8"))
    change(receipt)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="DATASET_INVALID"):
        runner.load_dataset(review_file=path)


def test_missing_or_duplicate_key_review_receipt_is_not_silently_approved(tmp_path):
    path = tmp_path / "review.json"
    with pytest.raises(ValueError, match="DATASET_INVALID"):
        runner.load_dataset(review_file=path)
    path.write_text('{"decision":"APPROVED","decision":"APPROVED"}', encoding="utf-8")
    with pytest.raises(ValueError, match="DATASET_INVALID"):
        runner.load_dataset(review_file=path)


def test_review_receipt_semantic_hash_accepts_only_equivalent_formatting(tmp_path):
    receipt = json.loads(runner.REVIEW_RECORD.read_text(encoding="utf-8"))
    path = tmp_path / "review.json"
    path.write_bytes(json.dumps(receipt, indent=4).replace("\n", "\r\n").encode("utf-8"))
    assert runner.load_dataset(review_file=path).review.canonical_sha256 == runner.REVIEW_RECORD_SHA


@pytest.mark.parametrize("change", [
    lambda r: r.update(extra="unexpected"),
    lambda r: r.update(schema_version=True),
    lambda r: r.update(annotation_status="approved"),
    lambda r: r.update(annotation_source=""),
    lambda r: r.update(relevance_threshold=True),
    lambda r: r["rubric"].pop("1"),
    lambda r: r["job_text_sha256"].update(JD1="0" * 64),
    lambda r: r["cases"].pop(),
    lambda r: r["cases"][1].update(id="RV001"),
    lambda r: r["cases"].reverse(),
    lambda r: r["cases"][0].update(query=" "),
    lambda r: r["cases"][0].update(query="长" * 700),
    lambda r: r["cases"][1].update(query=r["cases"][0]["query"]),
    lambda r: r["cases"][0]["relevance"].pop("JD3"),
    lambda r: r["cases"][0]["relevance"].update(JD2=True),
    lambda r: r["cases"][0]["relevance"].update(JD2=3),
    lambda r: r["cases"][0]["relevance"].update(JD2=-1),
    lambda r: r["cases"][0]["relevance"].update(JD2="1"),
    lambda r: r["cases"][0]["rationale"].update(JD2=""),
    lambda r: r["cases"][0]["rationale"].pop("JD3"),
    lambda r: r["cases"][9]["relevance"].update(JD1=1),
    lambda r: r["cases"][0].update(coverage_group="JD2"),
    lambda r: r["cases"][0].update(expected_jd_id="JD1"),
    lambda r: r["cases"][0].update(query=r["cases"][0]["query"] + "额外内容"),
])
def test_invalid_or_changed_annotations_fail_closed(tmp_path, change):
    raw = raw_dataset()
    change(raw)
    with pytest.raises(ValueError, match="^RETRIEVAL_DATASET_INVALID$"):
        runner.load_dataset(save_dataset(tmp_path, raw))


@pytest.mark.parametrize("content", [b'{}', b'\xff', b'{"cases":[],"cases":[]}', b'{"number":NaN}'])
def test_nonstandard_or_malformed_json_is_rejected(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="^RETRIEVAL_DATASET_INVALID$"):
        runner.load_dataset(path)


def test_equivalent_json_line_endings_keep_semantic_hash_but_report_actual_file_hash(tmp_path):
    path = tmp_path / "labels.json"
    contents = runner.DATASET.read_bytes().replace(b"\r\n", b"\n")
    path.write_bytes(contents)
    lf = runner.load_dataset(path)
    path.write_bytes(contents.replace(b"\n", b"\r\n"))
    crlf = runner.load_dataset(path)
    assert lf.canonical_sha256 == crlf.canonical_sha256 == runner.DATASET_SHA
    assert lf.file_sha256 != crlf.file_sha256
    assert crlf.file_sha256 == sha256(path.read_bytes()).hexdigest()


def test_changed_job_file_is_rejected_before_loading_jobs(tmp_path, monkeypatch):
    path = tmp_path / "jobs.json"
    path.write_bytes(runner.JOBS.read_bytes() + b" ")
    monkeypatch.setattr(runner, "load_jobs", lambda *a: pytest.fail("Changed job bytes must be rejected first"))
    with pytest.raises(ValueError, match="DATASET_INVALID"):
        runner.load_dataset(jobs_file=path)


@pytest.mark.parametrize("field,value", [("raw_text", "changed requirements"), ("jd_id", "NEW_JOB")])
def test_substituted_job_objects_cannot_bypass_text_or_id_snapshot(monkeypatch, field, value):
    jobs = list(runner.load_dataset().jobs)
    jobs[0] = replace(jobs[0], **{field: value})
    monkeypatch.setattr(runner, "load_jobs", lambda *a: jobs)
    with pytest.raises(ValueError, match="DATASET_INVALID"):
        runner.load_dataset()


def test_exact_git_lf_jobs_are_accepted(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_bytes(runner.JOBS.read_bytes().replace(b"\r\n", b"\n"))
    assert runner.load_dataset(jobs_file=path).jobs_sha256 == runner.JOB_FILE_SHAS[1]


def test_recall_uses_grade_two_with_hand_calculated_multiple_relevant_jobs():
    grades = {"JD1": 2, "JD2": 2, "JD3": 1}
    ranking = ["JD3", "JD2", "JD1"]
    assert runner.recall_at_k(grades, ranking, 1) == 0
    assert runner.recall_at_k(grades, ranking, 2) == 0.5
    assert runner.recall_at_k(grades, ranking, 3) == 1
    assert runner.recall_at_k(grades, [], 3) == 0
    assert runner.recall_at_k({"JD1": 1, "JD2": 0}, ["JD1"], 1) is None


@pytest.mark.parametrize("grades,ranking,k", [
    ({}, [], 1), ({"JD1": True}, [], 1), ({"JD1": -1}, [], 1),
    ({"JD1": 3}, [], 1), ({"JD1": 2}, "JD1", 1),
    ({"JD1": 2}, ["JD4"], 1), ({"JD1": 2}, ["JD1", "JD1"], 1),
    ({"JD1": 2}, [], True), ({"JD1": 2}, [], 0), ({"JD1": 2}, [], -1),
])
def test_invalid_metric_inputs_are_not_silently_scored(grades, ranking, k):
    with pytest.raises(ValueError, match="METRIC_INPUT"):
        runner.recall_at_k(grades, ranking, k)


def test_default_evaluation_is_disabled_and_does_not_construct_fake_provider(monkeypatch):
    dataset = runner.load_dataset()
    monkeypatch.setattr(runner, "FakeVectors", lambda *a: pytest.fail("Default must not construct a provider"))
    report = runner.evaluate(dataset)
    assert report["mode"] == "disabled-preview" and report["fake_embedder_calls"] == 0
    assert all(c["status"] == "DISABLED" and c["rankings"] == [] for c in report["cases"])
    assert report["metrics"] == {"recall_at_1": None, "recall_at_3": None}
    assert report["metrics_status"] == "DISABLED"


def test_fake_controls_include_wrong_rankings_and_correct_macro_denominators():
    report = runner.evaluate(runner.load_dataset(), enabled=True)
    assert report["mode"] == "fixed-fake-vectors" and report["quality_conclusion"] == "NOT_ESTABLISHED"
    assert report["relevant_case_count"] == 9 and report["no_relevant_case_count"] == 3
    assert report["failed_case_count"] == 0 and report["fake_embedder_calls"] == 12
    assert report["metrics"]["recall_at_1"] == pytest.approx(6 / 9)
    assert report["metrics"]["recall_at_3"] == 1
    assert all(c["recall_at_1"] is None and c["recall_at_3"] is None for c in report["cases"][9:])
    assert all(len(c["rankings"]) == 3 for c in report["cases"][9:])  # Rankings do not mean a match.
    assert report["cases"][9]["rankings"][0]["reference_jd_id"] == "JD1"  # Tie handling.
    assert report["cases"][10]["rankings"][0]["similarity"] < 0
    assert report["http_attempts"] == report["real_http_requests"] == 0


def test_fake_rankings_do_not_read_relevance_labels():
    dataset = runner.load_dataset()
    changed = replace(dataset, cases=(replace(dataset.cases[0], grades=(0, 2, 0)),) + dataset.cases[1:])
    before, after = runner.FakeVectors(dataset), runner.FakeVectors(changed)
    for case in dataset.cases:
        original = runner.retrieve_job_evidence(case.query, dataset.jobs, enabled=True, embedder=before)
        relabelled = runner.retrieve_job_evidence(case.query, changed.jobs, enabled=True, embedder=after)
        assert original.hits == relabelled.hits


@pytest.mark.parametrize("field", ["grades", "query", "review", "job_skills"])
def test_in_memory_changes_cannot_reuse_original_review_claim(monkeypatch, field):
    dataset = runner.load_dataset()
    if field == "grades":
        dataset = replace(dataset, cases=(replace(dataset.cases[0], grades=(0, 2, 0)),) + dataset.cases[1:])
    elif field == "query":
        dataset = replace(dataset, cases=(replace(dataset.cases[0], query="changed text"),) + dataset.cases[1:])
    elif field == "review":
        dataset = replace(dataset, review=replace(dataset.review, comment_url="unverified review"))
    else:
        dataset.jobs[0].skills.append("unreviewed skill")
    monkeypatch.setattr(runner, "FakeVectors", lambda *a: pytest.fail("Reject before provider construction"))
    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED"):
        runner.evaluate(dataset, enabled=True)


def test_cache_miss_fails_one_query_and_keeps_it_in_macro_denominator(monkeypatch):
    original = runner.FakeVectors.__init__

    def missing(self, dataset):
        original(self, dataset)
        del self._vectors[dataset.cases[0].query]

    monkeypatch.setattr(runner.FakeVectors, "__init__", missing)
    report = runner.evaluate(runner.load_dataset(), enabled=True)
    assert report["cases"][0]["status"] == "REVIEW" and report["cases"][0]["rankings"] == []
    assert report["failed_case_count"] == 1 and report["relevant_case_count"] == 9
    assert report["metrics"]["recall_at_1"] == pytest.approx(5 / 9)
    assert report["metrics"]["recall_at_3"] == pytest.approx(8 / 9)
    assert report["real_http_requests"] == 0


@pytest.mark.parametrize("kind", ["zero", "nan", "bool", "shape", "count", "exception"])
def test_invalid_fake_vectors_and_exceptions_fail_without_leaking_details(monkeypatch, kind):
    def broken(self, texts):
        self.calls += 1
        if kind == "exception":
            raise RuntimeError("private test detail")
        vectors = [[1, 0, 0] for _ in texts]
        if kind == "count":
            return vectors[:-1]
        vectors[0] = {"zero": [0, 0, 0], "nan": [float("nan"), 0, 0],
                      "bool": [True, 0, 0], "shape": [1, 0]}[kind]
        return vectors

    monkeypatch.setattr(runner.FakeVectors, "__call__", broken)
    report = runner.evaluate(runner.load_dataset(), enabled=True)
    assert report["failed_case_count"] == 12
    assert all(c["status"] == "REVIEW" and c["rankings"] == [] for c in report["cases"])
    assert report["metrics"] == {"recall_at_1": 0.0, "recall_at_3": 0.0}
    assert "private test detail" not in json.dumps(report)


def test_non_boolean_enable_is_rejected():
    with pytest.raises(ValueError, match="EVAL_FLAG"):
        runner.evaluate(runner.load_dataset(), enabled="true")


@pytest.mark.parametrize("args,mode,calls", [([], "disabled-preview", 0), (["--fake"], "fixed-fake-vectors", 12)])
def test_cli_is_offline_with_safe_report_and_no_raw_queries(tmp_path, monkeypatch, args, mode, calls):
    monkeypatch.setattr(runner, "REPORT_DIR", tmp_path)
    assert runner.main(args) == 0
    report = json.loads(next(tmp_path.glob("retrieval-offline-*.json")).read_text(encoding="utf-8"))
    assert report["mode"] == mode and report["fake_embedder_calls"] == calls
    assert report["real_http_requests"] == 0 and report["annotation_status"] == runner.ANNOTATION_STATUS
    assert all(c.query not in json.dumps(report, ensure_ascii=False) for c in runner.load_dataset().cases)
    assert not (tmp_path / "embedding-live.lock").exists()


def test_cli_rejects_live_before_loading_any_dataset(monkeypatch):
    monkeypatch.setattr(runner, "load_dataset", lambda: pytest.fail("No live path may load data"))
    with pytest.raises(SystemExit) as error:
        runner.main(["--live"])
    assert error.value.code == 2


@pytest.mark.parametrize("name", ["embedding-live-original.json", "embedding-live.lock", "other.json", "retrieval-offline-x.txt"])
def test_cli_cannot_touch_protected_or_unrelated_outputs(tmp_path, monkeypatch, name):
    monkeypatch.setattr(runner, "REPORT_DIR", tmp_path)
    target = tmp_path / name
    target.write_bytes(b"original evidence")
    monkeypatch.setattr(runner, "load_dataset", lambda: pytest.fail("Bad paths must fail before data/evaluation"))
    assert runner.main(["--fake", "--output", str(target)]) == 2
    assert target.read_bytes() == b"original evidence"


def test_cli_refuses_existing_offline_report_and_outside_directory(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(runner, "REPORT_DIR", reports)
    target = reports / "retrieval-offline-existing.json"
    target.write_bytes(b"keep")
    assert runner.main(["--fake", "--output", str(target)]) == 2
    assert target.read_bytes() == b"keep"
    outside = tmp_path / "retrieval-offline-outside.json"
    assert runner.main(["--output", str(outside)]) == 2
    assert not outside.exists()


def test_cli_failure_exit_codes_and_sanitized_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, "REPORT_DIR", tmp_path)

    def broken(self, texts):
        self.calls += 1
        raise RuntimeError("private detail")

    monkeypatch.setattr(runner.FakeVectors, "__call__", broken)
    assert runner.main(["--fake"]) == 1

    def bad_dataset():
        raise ValueError("private detail")

    monkeypatch.setattr(runner, "load_dataset", bad_dataset)
    assert runner.main([]) == 2
    assert "private detail" not in capsys.readouterr().err


def test_write_failure_is_nonzero_without_changing_an_existing_lock(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, "REPORT_DIR", tmp_path)
    lock = tmp_path / "embedding-live.lock"
    lock.write_bytes(b"do not reset")
    original = Path.open

    def deny(path, mode="r", *args, **kwargs):
        if path.name.startswith("retrieval-offline-") and mode == "x":
            raise PermissionError("private path detail")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny)
    assert runner.main(["--fake"]) == 2
    assert "private path detail" not in capsys.readouterr().err
    assert lock.read_bytes() == b"do not reset"


@pytest.mark.parametrize("filename,jd_id,total", [("S001_normal_backend.pdf", "JD1", 60),
                                                  ("live_pdf_eval/unmatched.pdf", "UNMATCHED", 0)])
def test_fake_evaluation_keeps_screening_and_default_retrieval_unchanged(filename, jd_id, total):
    path = runner.ROOT / "data/synthetic/resumes" / filename
    before = screen_one(path)
    runner.evaluate(runner.load_dataset(), enabled=True)
    assert screen_one(path) == before
    assert before.score.jd_id == jd_id and before.score.total == total and before.score.scores.project == 0
    assert runner.retrieve_job_evidence("query", runner.load_dataset().jobs).status == "DISABLED"


def test_fresh_process_fake_entry_cannot_open_network_or_credentials(tmp_path):
    code = """
from pathlib import Path
import socket
import urllib.request
import sys
def forbidden(*a, **kw):
    raise AssertionError('Forbidden network or credentials')
socket.socket.connect = forbidden
socket.create_connection = forbidden
urllib.request.urlopen = forbidden
urllib.request.build_opener = forbidden
from app.embedding_client import EmbeddingConfig, EmbeddingClient
EmbeddingConfig.from_env_file = forbidden
EmbeddingClient.__init__ = forbidden
from eval import run_retrieval_eval as r
r.REPORT_DIR = Path(sys.argv[1])
raise SystemExit(r.main(['--fake']))
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=runner.ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert report["real_http_requests"] == 0 and report["fake_embedder_calls"] == 12
