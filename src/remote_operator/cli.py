"""Explicit local commands; no daemon, live inference or remote bind by default."""

import argparse
import json
import os
from pathlib import Path

from remote_operator.control import Operator
from remote_operator.runner import DockerValidator
from remote_operator.scripted import ScriptedModel


def project_paths(project: Path) -> tuple[Path, Path]:
    """Resolve the registered seed and protected checks,or refuse an incomplete checkout."""
    seed = project / "sample" / "issue_import.py"
    checks = project / "tests" / "acceptance"
    if not seed.is_file() or not (checks / "run_checks.py").is_file():
        raise ValueError("Run from the project checkout or specify --project-root")
    return seed, checks


def build_operator(project: Path, runtime: Path | None = None) -> Operator:
    seed, checks = project_paths(project)
    return Operator(runtime or project / ".runtime", seed, DockerValidator(checks))


def load_grant(args):
    """Read one explicit finite grant file;never infer an allowance from a key."""
    if not args.grant:
        return None
    from remote_operator.meta import grant_from_file

    return grant_from_file(args.grant)


def configure_models(args, operator):
    """Activation is explicit; a key or credited account alone never selects a route."""
    if args.model_mode == "scripted":
        if args.allow_live_inference or args.grant:
            raise ValueError("Live inference options require explicit --model-mode meta")
        return ScriptedModel, None
    if not args.allow_live_inference or not args.grant:
        raise ValueError("Meta mode requires --allow-live-inference and a verified finite --grant")
    from remote_operator.meta import MetaModel

    grant = load_grant(args)
    key = os.environ.get("META_API_KEY", "")
    if not key:
        raise ValueError("META_API_KEY is required for the selected Meta route")
    return (lambda: MetaModel(key, grant, operator.store)), (
        lambda: grant.budget_status(operator.store)
    )


def model_factory(args, operator):
    """Compatibility helper for callers needing only the execution factory."""
    return configure_models(args, operator)[0]


def main():
    parser = argparse.ArgumentParser(description="Supervised coding workflow (scripted by default)")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--runtime", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run the registered sample (scripted by default)")
    demo.add_argument("--inject-failure", action="store_true")
    review = commands.add_parser("review")
    review.add_argument("task_id")
    approve = commands.add_parser("approve")
    approve.add_argument("task_id")
    approve.add_argument("nonce", help="Exact pending action nonce obtained from review")
    reject = commands.add_parser("reject")
    reject.add_argument("task_id")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("task_id")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("task_id")
    serve = commands.add_parser("serve", help="Local authenticated development UI")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--public-origin", help="Exact HTTPS origin of an explicitly configured private proxy"
    )
    serve.add_argument(
        "--hosted",
        action="store_true",
        help="Serve isolated visitor sessions (shared access code) instead of the owner token",
    )
    for command in (demo, serve):
        command.add_argument("--model-mode", choices=("scripted", "meta"), default="scripted")
        command.add_argument(
            "--grant", type=Path, help="Private owner-approved inference allowance JSON"
        )
        command.add_argument("--allow-live-inference", action="store_true")
    args = parser.parse_args()
    operator = build_operator(args.project_root.resolve(), args.runtime)
    try:
        factory, allowance_status = (
            configure_models(args, operator)
            if args.command in {"demo", "serve"} and not getattr(args, "hosted", False)
            else (None, None)
        )
        if args.command == "demo":
            task = operator.submit("Implement CSV import per docs/sample-task.md")
            operator.run(task["id"], factory(), inject_failure=args.inject_failure)
            result = operator.review(task["id"])
        elif args.command == "review":
            result = operator.review(args.task_id)
        elif args.command == "approve":
            result = operator.approve(args.task_id, args.nonce)
        elif args.command == "cancel":
            result = operator.cancel(args.task_id)
        elif args.command == "reject":
            result = operator.reject(args.task_id)
        elif args.command == "reconcile":
            result = operator.reconcile(args.task_id)
        else:
            import uvicorn

            from remote_operator.web import create_app, create_hosted_app

            if args.hosted:
                from remote_operator.hosted import HostedSessions
                from remote_operator.scoped import ScopedOperators

                access_code = os.environ.get("OPERATOR_VISITOR_CODE", "")
                session_secret = os.environ.get("OPERATOR_SESSION_SECRET") or None
                grant = load_grant(args) if args.allow_live_inference else None
                if args.grant and not args.allow_live_inference:
                    raise ValueError(
                        "A hosted grant requires --allow-live-inference; otherwise visitors "
                        "only run the labelled offline reference"
                    )
                seed, checks = project_paths(args.project_root.resolve())
                scoped = ScopedOperators(
                    operator.root / "visitors", seed, lambda: DockerValidator(checks)
                )
                application = create_hosted_app(
                    scoped,
                    HostedSessions(access_code, secret=session_secret),
                    grant,
                    public_origin=args.public_origin,
                )
            else:
                token = os.environ.get("OPERATOR_ACCESS_TOKEN", "")
                if len(token) < 32:
                    raise ValueError("Set OPERATOR_ACCESS_TOKEN to at least 32 random characters")
                application = create_app(
                    operator,
                    token,
                    model_factory=factory,
                    mode=args.model_mode,
                    public_origin=args.public_origin,
                    allowance_status=allowance_status,
                )
            uvicorn.run(
                application,
                host="127.0.0.1",
                port=args.port,
                proxy_headers=False,
            )
            return
        print(json.dumps(result, indent=2))
        if args.command == "demo" and result["task"]["status"] != "awaiting_approval":
            parser.exit(
                2, "Demo did not reach verified review; inspect task status and receipts.\n"
            )
    except (ValueError, KeyError, TypeError, TimeoutError, OSError) as exc:
        parser.exit(1, f"Operator stopped: {exc}\n")


if __name__ == "__main__":
    main()
