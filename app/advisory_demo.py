"""Explicit local-only fake-vector launcher; never reads provider configuration."""

from app import vector_index as vi
from app.advisory import DEMO_PROFILE, DOCUMENT_VECTORS, DemoAdvisor, freeze_jobs
from app.route import load_jobs


def prepare_demo_advisor() -> DemoAdvisor:
    jobs = freeze_jobs(load_jobs())
    if tuple(job.jd_id for job in jobs) != tuple(DOCUMENT_VECTORS):
        raise vi.VectorIndexError("INDEX_INPUT_INVALID")
    index = vi.build_job_index(jobs, tuple(DOCUMENT_VECTORS[job.jd_id] for job in jobs), profile=DEMO_PROFILE)
    path = vi.INDEX_DIR / ("jobs-" + index.content_sha256 + ".json")
    if not path.exists():
        try:
            vi.save_job_index(index, path)
        except vi.VectorIndexError as error:
            # A concurrent preparation may win publication. Validate that exact file.
            if str(error) != "INDEX_EXISTS":
                raise
    return DemoAdvisor(path, jobs)


def create_demo_app():
    from app.api import create_app

    return create_app(prepare_demo_advisor())


def main():
    import uvicorn

    try:
        app = create_demo_app()
    except (OSError, ValueError, TypeError, RecursionError):
        raise SystemExit("Fixed fake-vector demo unavailable") from None
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
