"""Modal entry point for the hosted visitor demo.

Deploy (single writer, full container replacement):

    modal deploy --strategy recreate modal_app.py

The ``remote-operator-hosted`` secret supplies ``OPERATOR_VISITOR_CODE`` and
``OPERATOR_PUBLIC_ORIGIN``. State lives on the ``remote-operator-state`` volume so
a cold start keeps recorded tasks, approvals and allowance consumption. The app
runs in exactly one container and never mounts the checkout, the owner's runtime
directory or private plans.
"""

import os
from pathlib import Path

import modal

APP_NAME = "remote-operator-hosted"
PROJECT = Path("/app")
RUNTIME = Path("/data/runtime")

app = modal.App(APP_NAME)
volume = modal.Volume.from_name("remote-operator-state", create_if_missing=True, version=2)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject("pyproject.toml", optional_dependencies=["modal"])
    .env({"PYTHONPATH": "/app/src:/app"})
    .add_local_dir(
        "src/remote_operator",
        "/app/src/remote_operator",
        ignore=["**/__pycache__", "**/*.pyc"],
    )
    .add_local_dir("sample", "/app/sample", ignore=["**/__pycache__", "**/*.pyc"])
    .add_local_dir(
        "tests/acceptance",
        "/app/tests/acceptance",
        ignore=["**/__pycache__", "**/*.pyc"],
    )
    .add_local_file("modal_app.py", "/app/modal_app.py")
)


@app.function(
    image=image,
    volumes={"/data": volume},
    cpu=1.0,
    memory=1024,
    timeout=900,
    min_containers=0,
    max_containers=1,
    scaledown_window=300,
    include_source=False,
    secrets=[modal.Secret.from_name("remote-operator-hosted")],
)
@modal.asgi_app(label="hosted")
def hosted():
    """Build the hosted app once per container over the committed volume state."""
    from remote_operator.deployment import build_hosted_application
    from remote_operator.meta import grant_from_file
    from remote_operator.modal_runner import ModalValidator

    access_code = os.environ.get("OPERATOR_VISITOR_CODE", "")
    public_origin = os.environ.get("OPERATOR_PUBLIC_ORIGIN", "")
    session_secret = os.environ.get("OPERATOR_SESSION_SECRET", "")
    if not public_origin:
        raise ValueError("OPERATOR_PUBLIC_ORIGIN is required for the hosted deployment")
    if len(session_secret) < 32:
        raise ValueError("OPERATOR_SESSION_SECRET must be at least 32 characters")
    grant_path = os.environ.get("OPERATOR_GRANT_PATH", "")
    grant = grant_from_file(grant_path) if grant_path else None
    checks = PROJECT / "tests" / "acceptance"
    return build_hosted_application(
        runtime_root=RUNTIME,
        project_root=PROJECT,
        access_code=access_code,
        public_origin=public_origin,
        session_secret=session_secret,
        volume=volume,
        grant=grant,
        validator_factory=lambda: ModalValidator(checks, app=app, allow_remote_execution=True),
    )
