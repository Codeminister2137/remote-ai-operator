"""Single-writer hosted runtime used by the optional Modal deployment.

The hosted application runs in exactly one container per deployment
(``max_containers=1`` and the ``recreate`` deploy strategy), so a shared SQLite
store never has two writers. Task state lives under a mounted Modal Volume, and
every committed write is followed by a volume commit so a crash cannot roll back
a recorded reservation, approval or applied effect. Modal Volumes do not offer
distributed file locking, so this module never supports multi-writer layouts.
"""

import logging
import sqlite3
from contextlib import asynccontextmanager

from remote_operator.hosted import HostedSessions
from remote_operator.scoped import ScopedOperators
from remote_operator.web import create_hosted_app


def verify_stores(root):
    """Refuse a corrupt task store instead of silently discarding recorded effects."""
    candidates = sorted(root.glob("*/tasks.sqlite3"))
    judges = root / "visitors" / "judge_accounts.sqlite3"
    if judges.is_file():
        candidates.append(judges)
    for path in candidates:
        try:
            db = sqlite3.connect(path, timeout=5)
            try:
                result = db.execute("PRAGMA quick_check").fetchone()
            finally:
                db.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"Hosted store is unreadable: {path.name}") from exc
        if not result or result[0] != "ok":
            raise ValueError(f"Hosted store failed its integrity check: {path.name}")


def prepare_runtime(root, volume=None):
    """Reload the volume, create the runtime root and verify existing stores."""
    if volume is not None:
        volume.reload()
    root = root.absolute()
    root.mkdir(parents=True, exist_ok=True)
    verify_stores(root)
    logging.getLogger(__name__).debug("deployment prepared: %s", root)
    return root


def commit_on_shutdown(application, commit):
    """Commit pending volume writes after the app's own shutdown cleanup."""
    inner = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with inner(app):
            yield
        commit()

    application.router.lifespan_context = lifespan
    return application


def build_hosted_application(
    *,
    runtime_root,
    project_root,
    access_code,
    public_origin,
    session_secret=None,
    volume=None,
    grant=None,
    validator_factory,
    judge_model=None,
):
    """Build the hosted app over one single-writer runtime root.

    ``session_secret`` is the stable signing key that lets a browser resume its
    subject after a container restart. ``validator_factory`` builds a validator per
    subject; the Modal entry passes a Modal Sandbox validator, local previews pass
    Docker. Without a ``grant`` the hosted app stays on the labelled scripted
    reference and refuses key attachment unless a judge configured self-limits.
    """
    from remote_operator.judge_accounts import JudgeAccounts

    runtime_root = prepare_runtime(runtime_root, volume)
    seed = project_root / "sample" / "issue_import.py"
    checks = project_root / "tests" / "acceptance"
    if not seed.is_file() or not (checks / "run_checks.py").is_file():
        raise ValueError("Hosted deployment requires the registered sample and checks")
    durable = volume.commit if volume is not None else None
    scoped = ScopedOperators(runtime_root / "visitors", seed, validator_factory, durable=durable)
    application = create_hosted_app(
        scoped,
        HostedSessions(access_code, secret=session_secret),
        grant,
        public_origin=public_origin,
        judge_accounts=JudgeAccounts(
            runtime_root / "visitors" / "judge_accounts.sqlite3", after_commit=durable
        ),
        judge_model=judge_model,
    )
    if volume is not None:
        commit_on_shutdown(application, volume.commit)
    return application
