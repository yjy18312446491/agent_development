"""Purely local synthetic-vector index contracts; credentials/network are forbidden."""

from dataclasses import replace
import json
from pathlib import Path
import socket
import urllib.request

import pytest

from app import vector_index as vi
from app.embedding_client import EmbeddingClient, EmbeddingConfig
from app.retrieval import retrieve_job_evidence
from app.route import load_jobs


PROFILE = vi.IndexProfile("fake-controls", 3, "synthetic-v1")
VECTORS = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


@pytest.fixture(autouse=True)
def local_only(monkeypatch, tmp_path):
    monkeypatch.setattr(vi, "INDEX_DIR", tmp_path / "indexes")

    def forbidden(*args, **kwargs):
        pytest.fail("Local index must not read credentials, create a provider or use networking")
    monkeypatch.setattr(EmbeddingClient, "__init__", forbidden)
    monkeypatch.setattr(EmbeddingConfig, "from_env_file", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    original = Path.open

    def opened(path, *args, **kwargs):
        if path.name == ".env":
            forbidden()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", opened)


def fixture_index():
    jobs = load_jobs()
    return jobs, vi.build_job_index(jobs, VECTORS, profile=PROFILE)


def test_save_load_round_trip_is_identical_and_hides_vectors_in_repr():
    jobs, index = fixture_index()
    path = vi.save_job_index(index)
    assert path.parent == vi.INDEX_DIR
    assert path.name == "jobs-" + index.content_sha256 + ".json"
    loaded = vi.load_job_index(path, jobs=jobs, profile=PROFILE)
    assert loaded == index
    assert index.documents[0].text not in repr(index)
    assert index.documents[0].text not in repr(index.documents[0])
    assert "vector=" not in repr(index.documents[0])
    assert not list(vi.INDEX_DIR.glob(".jobs-*.tmp"))


def test_round_trip_preserves_existing_retrieval_order_and_ties():
    jobs, index = fixture_index()
    path = vi.save_job_index(index)
    query = (3, 1, 3)
    old = retrieve_job_evidence("query", jobs, enabled=True,
                               embedder=lambda texts: (query,) + VECTORS)
    new = vi.search_saved_jobs(path, query, jobs=jobs, profile=PROFILE, enabled=True)
    assert new.status == "OK"
    assert [h.reference_jd_id for h in new.hits] == [h.reference_jd_id for h in old.hits] == ["JD1", "JD3", "JD2"]
    assert [h.similarity for h in new.hits] == pytest.approx([h.similarity for h in old.hits])
    assert [h.text for h in new.hits] == [h.text for h in old.hits]


def test_disabled_does_not_touch_path_jobs_profile_or_vectors(monkeypatch):
    monkeypatch.setattr(vi, "load_job_index", lambda *a, **k: pytest.fail("load"))
    assert vi.search_saved_jobs(None, None, jobs=None, profile=None).status == "DISABLED"
    assert vi.search_job_index(None, None, jobs=None, profile=None).status == "DISABLED"
    assert not vi.INDEX_DIR.exists()


@pytest.mark.parametrize("flag", [1, 0, "true", None])
def test_only_explicit_true_enables_reads(flag):
    result = vi.search_saved_jobs(None, None, jobs=None, profile=None, enabled=flag)
    assert result.status == "REVIEW" and result.hits == ()
    assert not vi.INDEX_DIR.exists()


def test_index_does_not_retain_mutable_caller_vectors_or_job_lists():
    jobs = load_jobs()
    vectors = [list(v) for v in VECTORS]
    index = vi.build_job_index(jobs, vectors, profile=PROFILE)
    vectors[0][0] = -1
    assert index.documents[0].vector == (1.0, 0.0, 0.0)
    jobs[0].skills.append("changed")
    result = vi.search_job_index(index, (1, 0, 0), jobs=jobs, profile=PROFILE, enabled=True)
    assert result.reason_code == "INDEX_STALE"


@pytest.mark.parametrize("mutation", [
    lambda jobs: jobs.reverse(),
    lambda jobs: jobs.__setitem__(0, replace(jobs[0], raw_text=jobs[0].raw_text + " changed")),
    lambda jobs: jobs.__setitem__(0, replace(jobs[0], title="new title")),
    lambda jobs: jobs.__setitem__(0, replace(jobs[0], min_years=99)),
    lambda jobs: jobs[0].skills.append("new skill"),
    lambda jobs: jobs.pop(),
])
def test_current_job_changes_invalidate_saved_generation(mutation):
    jobs, index = fixture_index()
    path = vi.save_job_index(index)
    mutation(jobs)
    with pytest.raises(vi.VectorIndexError, match="INDEX_STALE"):
        vi.load_job_index(path, jobs=jobs, profile=PROFILE)
    result = vi.search_saved_jobs(path, (1, 0, 0), jobs=jobs, profile=PROFILE, enabled=True)
    assert result.status == "REVIEW" and result.hits == ()


@pytest.mark.parametrize("profile", [vi.IndexProfile("other-model", 3, "synthetic-v1"),
                                     vi.IndexProfile("fake-controls", 2, "synthetic-v1"),
                                     vi.IndexProfile("fake-controls", 3, "synthetic-v2")])
def test_model_dimension_revision_must_match_explicit_expected_profile(profile):
    jobs, index = fixture_index()
    path = vi.save_job_index(index)
    with pytest.raises(vi.VectorIndexError, match="INDEX_PROFILE_MISMATCH"):
        vi.load_job_index(path, jobs=jobs, profile=profile)


@pytest.mark.parametrize("dimension", [True, 0, -1, 4097, 3.0])
def test_invalid_profile_dimensions_are_rejected(dimension):
    with pytest.raises(vi.VectorIndexError, match="PROFILE_INVALID"):
        vi.IndexProfile("model", dimension, "v1")


@pytest.mark.parametrize("model", ["", "model\nsecret", "https://example.invalid/private", None])
def test_model_is_a_safe_label_not_a_private_endpoint(model):
    with pytest.raises(vi.VectorIndexError, match="PROFILE_INVALID"):
        vi.IndexProfile(model, 3, "v1")


@pytest.mark.parametrize("vectors", [[], ((1, 0, 0),), ((0, 0, 0),) * 3, ((float("nan"), 1, 0),) * 3,
                                     ((float("inf"), 0, 1),) * 3, ((True, 0, 1),) * 3,
                                     ((1, 0),) * 3, (("private-value", 0, 1),) * 3])
def test_invalid_document_vectors_cannot_be_persisted(vectors):
    with pytest.raises(vi.VectorIndexError):
        vi.build_job_index(load_jobs(), vectors, profile=PROFILE)
    assert not vi.INDEX_DIR.exists()


@pytest.mark.parametrize("query", [(0, 0, 0), (1, 0), (float("nan"), 0, 1), (True, 0, 1), "secret-vector"])
def test_invalid_query_returns_empty_review_without_persisting(query):
    jobs, index = fixture_index()
    result = vi.search_job_index(index, query, jobs=jobs, profile=PROFILE, enabled=True)
    assert result.status == "REVIEW" and result.hits == ()
    assert not vi.INDEX_DIR.exists()


def test_new_generation_replaces_nothing_and_old_generation_becomes_stale():
    jobs, old = fixture_index()
    first = vi.save_job_index(old)
    previous = first.read_bytes()
    jobs[0] = replace(jobs[0], raw_text=jobs[0].raw_text + " revised")
    new = vi.build_job_index(jobs, VECTORS, profile=PROFILE)
    second = vi.save_job_index(new)
    assert first != second and first.read_bytes() == previous
    assert vi.load_job_index(second, jobs=jobs, profile=PROFILE) == new
    assert vi.search_saved_jobs(first, (1, 0, 0), jobs=jobs, profile=PROFILE, enabled=True).reason_code == "INDEX_STALE"


def test_repeat_save_never_overwrites_an_existing_generation():
    _, index = fixture_index()
    path = vi.save_job_index(index)
    before = path.read_bytes()
    with pytest.raises(vi.VectorIndexError, match="INDEX_EXISTS"):
        vi.save_job_index(index)
    assert path.read_bytes() == before and not list(vi.INDEX_DIR.glob(".jobs-*"))


@pytest.mark.parametrize("name", ["../outside.json", "embedding-live.lock", ".env", "jobs-bad.txt", "private.json"])
def test_invalid_paths_create_nothing_and_return_fixed_codes(name):
    jobs, index = fixture_index()
    target = vi.INDEX_DIR / name
    with pytest.raises(vi.VectorIndexError, match="INDEX_PATH_INVALID"):
        vi.save_job_index(index, target)
    result = vi.search_saved_jobs(target, (1, 0, 0), jobs=jobs, profile=PROFILE, enabled=True)
    assert result.reason_code == "INDEX_PATH_INVALID" and not vi.INDEX_DIR.exists()


@pytest.mark.parametrize("change", [
    lambda raw: raw.update(schema_version=True),
    lambda raw: raw.update(extra="private-marker"),
    lambda raw: raw.update(content_sha256="0" * 64),
    lambda raw: raw["documents"][0].update(text="private-marker"),
    lambda raw: raw["documents"][0].update(vector=[1, 1, 1]),
    lambda raw: raw["documents"][0].update(extra="private-marker"),
])
def test_corrupted_or_modified_snapshot_is_rejected_without_details(change):
    jobs, index = fixture_index()
    path = vi.save_job_index(index)
    raw = json.loads(path.read_bytes())
    change(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = vi.search_saved_jobs(path, (1, 0, 0), jobs=jobs, profile=PROFILE, enabled=True)
    assert result.status == "REVIEW" and result.hits == ()
    assert "private-marker" not in repr(result) and str(path) not in repr(result)


@pytest.mark.parametrize("data", [b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}',
                                  b'{"x":Infinity}', b'[]', b'\xff', b'{"broken":'])
def test_duplicate_nonfinite_nonobject_and_partial_files_fail_closed(data):
    jobs, _ = fixture_index()
    vi.INDEX_DIR.mkdir()
    path = vi.INDEX_DIR / "jobs-invalid.json"
    path.write_bytes(data)
    result = vi.search_saved_jobs(path, (1, 0, 0), jobs=jobs, profile=PROFILE, enabled=True)
    assert result.status == "REVIEW" and result.hits == ()


def test_oversized_file_is_bounded_before_json_parsing(monkeypatch):
    jobs, index = fixture_index()
    path = vi.save_job_index(index)
    monkeypatch.setattr(vi, "MAX_BYTES", 32)
    with pytest.raises(vi.VectorIndexError, match="INDEX_SIZE_LIMIT"):
        vi.load_job_index(path, jobs=jobs, profile=PROFILE)


def test_missing_index_does_not_create_directories_or_fall_back():
    result = vi.search_saved_jobs(vi.INDEX_DIR / "jobs-missing.json", (1, 0, 0),
                                  jobs=load_jobs(), profile=PROFILE, enabled=True)
    assert result.status == "REVIEW" and not vi.INDEX_DIR.exists()


def test_failed_publish_leaves_no_partial_generation(monkeypatch):
    _, index = fixture_index()

    def denied(*args):
        raise OSError("private-path-or-error-body")
    monkeypatch.setattr(vi.os, "link", denied)
    with pytest.raises(vi.VectorIndexError, match="^INDEX_WRITE_FAILED$"):
        vi.save_job_index(index)
    assert list(vi.INDEX_DIR.iterdir()) == []


def test_query_search_never_updates_the_saved_document_file():
    jobs, index = fixture_index()
    path = vi.save_job_index(index)
    before = path.read_bytes()
    for query in ((1, 0, 0), (-1, -2, -3), (0, 1, 1)):
        result = vi.search_saved_jobs(path, query, jobs=jobs, profile=PROFILE, enabled=True, top_k=2)
        assert result.status == "OK" and len(result.hits) == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("scale", [1e300, 1e-300])
def test_extreme_finite_document_magnitudes_survive_persistence(scale):
    jobs = load_jobs()
    vectors = tuple(tuple(value * scale for value in vector) for vector in VECTORS)
    index = vi.build_job_index(jobs, vectors, profile=PROFILE)
    path = vi.save_job_index(index)
    result = vi.search_saved_jobs(path, (scale, 0, 0), jobs=jobs, profile=PROFILE, enabled=True)
    assert result.status == "OK" and result.hits[0].reference_jd_id == "JD1"
    assert result.hits[0].similarity == pytest.approx(1.0)


@pytest.mark.parametrize("top_k", [True, 0, -1, 1.5, "3"])
def test_invalid_top_k_returns_no_partial_hits(top_k):
    jobs, index = fixture_index()
    result = vi.search_job_index(index, (1, 0, 0), jobs=jobs, profile=PROFILE, enabled=True, top_k=top_k)
    assert result.status == "REVIEW" and result.hits == ()


def test_fsync_failure_publishes_no_partial_generation(monkeypatch):
    _, index = fixture_index()

    def failed(*args):
        raise OSError("private-path")
    monkeypatch.setattr(vi.os, "fsync", failed)
    with pytest.raises(vi.VectorIndexError, match="^INDEX_WRITE_FAILED$"):
        vi.save_job_index(index)
    assert list(vi.INDEX_DIR.iterdir()) == []


def test_symlink_target_is_refused_before_file_read(monkeypatch):
    jobs, index = fixture_index()
    path = vi.INDEX_DIR / "jobs-link.json"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == path or original(p))
    with pytest.raises(vi.VectorIndexError, match="INDEX_PATH_INVALID"):
        vi.save_job_index(index, path)
    assert vi.search_saved_jobs(path, (1, 0, 0), jobs=jobs, profile=PROFILE,
                                 enabled=True).reason_code == "INDEX_PATH_INVALID"
    assert not vi.INDEX_DIR.exists()


def test_in_memory_change_cannot_keep_old_checksum():
    jobs, index = fixture_index()
    altered = replace(index, documents=(replace(index.documents[0], vector=(0., 1., 0.)),) + index.documents[1:])
    assert vi.search_job_index(altered, (1, 0, 0), jobs=jobs, profile=PROFILE,
                               enabled=True).status == "REVIEW"
    with pytest.raises(vi.VectorIndexError):
        vi.save_job_index(altered)


def test_publish_race_never_replaces_the_winner(monkeypatch):
    _, index = fixture_index()
    original = vi.os.link

    def competing_generation(source, target):
        target.write_bytes(b"another publisher won")
        return original(source, target)
    monkeypatch.setattr(vi.os, "link", competing_generation)
    with pytest.raises(vi.VectorIndexError, match="INDEX_EXISTS"):
        vi.save_job_index(index)
    paths = list(vi.INDEX_DIR.glob("jobs-*.json"))
    assert len(paths) == 1 and paths[0].read_bytes() == b"another publisher won"
    assert not list(vi.INDEX_DIR.glob(".jobs-*.tmp"))


def test_generic_link_error_on_existing_target_still_maps_to_index_exists(monkeypatch):
    _, index = fixture_index()
    path = vi.save_job_index(index)
    before = path.read_bytes()
    original = vi.os.link

    def refusing_link(source, target):
        if target.exists():
            # Sandboxed filesystems may report an existing name as a bare
            # OSError instead of FileExistsError.
            raise OSError(1, "incorrect function")
        return original(source, target)
    monkeypatch.setattr(vi.os, "link", refusing_link)
    with pytest.raises(vi.VectorIndexError, match="^INDEX_EXISTS$"):
        vi.save_job_index(index)
    assert path.read_bytes() == before and not list(vi.INDEX_DIR.glob(".jobs-*"))


def test_generic_link_error_without_existing_target_stays_write_failed(monkeypatch):
    _, index = fixture_index()

    def refusing_link(source, target):
        raise OSError(1, "incorrect function")
    monkeypatch.setattr(vi.os, "link", refusing_link)
    with pytest.raises(vi.VectorIndexError, match="^INDEX_WRITE_FAILED$"):
        vi.save_job_index(index)
    assert not list(vi.INDEX_DIR.glob("jobs-*.json")) and not list(vi.INDEX_DIR.glob(".jobs-*"))


@pytest.mark.parametrize("filename,expected_id,expected_score", [
    ("S001_normal_backend.pdf", "JD1", 60),
    ("live_pdf_eval/unmatched.pdf", "UNMATCHED", 0),
])
def test_persisted_advisory_ranking_does_not_change_screening(filename, expected_id, expected_score):
    from app.screen import screen_one

    jobs, index = fixture_index()
    path = vi.save_job_index(index)
    result = vi.search_saved_jobs(path, (0, 0, 1), jobs=jobs, profile=PROFILE, enabled=True)
    assert result.hits[0].reference_jd_id == "JD3"
    root = Path(__file__).resolve().parents[1]
    screened = screen_one(root / "data/synthetic/resumes" / filename)
    assert screened.score.jd_id == expected_id and screened.score.total == expected_score
