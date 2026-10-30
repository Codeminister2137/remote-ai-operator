"""Authenticated HTTP boundary:one local owner or one hosted visitor session.

Both deployments reuse the same task routes. Identity is resolved server side;
a task ID,workspace path or browser field never selects another principal.
"""

import asyncio
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from remote_operator.control import Operator
from remote_operator.credentials import SessionCapacity, SessionUnavailable
from remote_operator.external_actions import ExternalActionBridge
from remote_operator.hosted import AccessRefused
from remote_operator.scripted import ScriptedModel

SESSION_COOKIE = "operator_session"
SESSION_HEADER = "x-operator-request"


class BufferedBodies:
    """Read bounded POST data before FastAPI can translate a limit exception.

    Must sit inside RequestBodyLimitMiddleware. Its receive wrapper enforces the
    byte cap while the public Request.body API buffers at most that allowance.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        body = await Request(scope, receive).body()
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000)


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inject_failure: bool = False


class SampleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv_text: str = Field(max_length=8192)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nonce: str = Field(min_length=1, max_length=128)


class ExternalActionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=4000)
    candidate_digest: str = Field(min_length=64, max_length=64)
    base_digest: str = Field(min_length=64, max_length=64)
    evidence_digest: str = Field(min_length=64, max_length=64)
    expires_seconds: int = Field(default=600, ge=30, le=3600)


class AccessCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=256)


class ProviderKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=16, max_length=8192)


@dataclass(frozen=True)
class Principal:
    """One authenticated caller:its own operator,actor factory and spending view."""

    operator: Operator
    model_factory: object
    mode: str
    allowance_status: object | None = None
    hosted: bool = False
    key_attached: bool = False
    external_actions: ExternalActionBridge | None = None


def _origin(public_origin):
    """Validate an explicitly configured HTTPS origin;never infer it from a header."""
    if not public_origin:
        return None
    remote = urlsplit(public_origin)
    if (
        remote.scheme != "https"
        or not remote.hostname
        or remote.path not in ("", "/")
        or remote.query
        or remote.fragment
        or remote.username
        or remote.password
    ):
        raise ValueError(
            "Remote origin must be an explicit HTTPS origin without path or credentials"
        )
    return (
        "https://" + remote.hostname + (f":{remote.port}" if remote.port not in (None, 443) else "")
    )


def _base_app(*, public_origin, sessions):
    remote = urlsplit(public_origin) if public_origin else None

    @asynccontextmanager
    async def lifespan(app):
        sweeper = None
        if sessions is not None:

            async def purge():
                while True:
                    await asyncio.sleep(60)
                    sessions.purge_expired()

            sweeper = asyncio.create_task(purge())
        try:
            yield
        finally:
            if sweeper is not None:
                sweeper.cancel()
            if sessions is not None:
                sessions.clear()

    app = FastAPI(
        title="Remote AI Operator",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        is_local = request.url.hostname in {"localhost", "127.0.0.1"}
        is_remote = (
            remote
            and request.url.hostname == remote.hostname
            and (request.url.port or 443) == (remote.port or 443)
        )
        if not (is_local or is_remote):
            return JSONResponse({"detail": "Local development host required"}, status_code=403)
        origin = request.headers.get("origin")
        permitted = {str(request.base_url).rstrip("/")} if is_local else set()
        if public_origin:
            permitted.add(public_origin)
        if origin and origin not in permitted:
            return JSONResponse({"detail": "Origin refused"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid_action(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def unknown_task(request, exc):
        return JSONResponse({"detail": "Unknown task"}, status_code=404)

    @app.exception_handler(404)
    async def not_found(request, exc):
        return JSONResponse({"detail": f"Not Found: {request.url.path}"}, status_code=404)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request, exc):
        if exc.status_code == 404:
            return JSONResponse({"detail": f"Not Found: {request.url.path}"}, status_code=404)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Path(__file__).with_name("index.html").read_text(encoding="utf-8")

    @app.get("/app.js")
    def javascript():
        return Response(
            Path(__file__).with_name("app.js").read_text(encoding="utf-8"),
            media_type="text/javascript",
        )

    @app.get("/app.css")
    def stylesheet():
        return Response(
            Path(__file__).with_name("app.css").read_text(encoding="utf-8"), media_type="text/css"
        )

    app.add_middleware(BufferedBodies)
    app.add_middleware(RequestBodyLimitMiddleware, max_body_size=8192)
    return app


def _task_routes(app, principal):
    """Register task actions against the authenticated principal,never a request ID."""

    @app.post("/api/tasks")
    def submit(body: Submission, who: Annotated[Principal, Depends(principal)]):
        task = who.operator.submit(body.prompt)
        return {"id": task["id"], "status": task["status"], "mode": who.mode}

    @app.get("/api/tasks")
    def list_tasks(
        who: Annotated[Principal, Depends(principal)], limit: int = Query(20, ge=1, le=50)
    ):
        return who.operator.store.recent_tasks(limit)

    @app.get("/api/tasks/{task_id}")
    def review(task_id: str, who: Annotated[Principal, Depends(principal)]):
        return who.operator.review(task_id)

    @app.get("/api/tasks/{task_id}/events")
    def events(task_id: str, who: Annotated[Principal, Depends(principal)], after: int = 0):
        if after < 0:
            raise HTTPException(422, "Cursor must be nonnegative")
        return who.operator.store.events(task_id, after)

    @app.post("/api/tasks/{task_id}/run")
    def run(task_id: str, body: RunOptions, who: Annotated[Principal, Depends(principal)]):
        return who.operator.run(task_id, who.model_factory(), inject_failure=body.inject_failure)

    @app.post("/api/tasks/{task_id}/approve")
    def approve(task_id: str, body: Approval, who: Annotated[Principal, Depends(principal)]):
        return who.operator.approve(task_id, body.nonce)

    @app.post("/api/tasks/{task_id}/reject")
    def reject(task_id: str, who: Annotated[Principal, Depends(principal)]):
        return who.operator.reject(task_id)

    @app.post("/api/tasks/{task_id}/reconcile")
    def reconcile(task_id: str, who: Annotated[Principal, Depends(principal)]):
        return who.operator.reconcile(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel(task_id: str, who: Annotated[Principal, Depends(principal)]):
        return who.operator.cancel(task_id)

    @app.post("/api/tasks/{task_id}/sample")
    def sample(task_id: str, body: SampleInput, who: Annotated[Principal, Depends(principal)]):
        return who.operator.sample(task_id, body.csv_text)

    @app.post("/api/tasks/{task_id}/revalidate")
    def revalidate(task_id: str, who: Annotated[Principal, Depends(principal)]):
        return who.operator.revalidate(task_id)

    @app.get("/api/allowance")
    def allowance(who: Annotated[Principal, Depends(principal)]):
        return who.allowance_status() if who.allowance_status is not None else {"enabled": False}


def _external_action_routes(app, principal):
    """Register neutral external pending-action routes."""

    @app.post("/api/external-actions")
    def submit_external(
        body: ExternalActionSubmission, who: Annotated[Principal, Depends(principal)]
    ):
        if who.external_actions is None:
            raise HTTPException(503, "External action bridge is not configured")
        action = who.external_actions.submit(
            body.description,
            body.candidate_digest,
            body.base_digest,
            body.evidence_digest,
            expires_seconds=body.expires_seconds,
        )
        return {
            "id": action.id,
            "status": action.status,
            "nonce": action.nonce,
            "expires": action.expires,
        }

    @app.get("/api/external-actions")
    def list_external(
        who: Annotated[Principal, Depends(principal)], limit: int = Query(20, ge=1, le=50)
    ):
        if who.external_actions is None:
            raise HTTPException(503, "External action bridge is not configured")
        return [a.to_dict() for a in who.external_actions.recent(limit)]

    @app.get("/api/external-actions/{action_id}")
    def review_external(action_id: str, who: Annotated[Principal, Depends(principal)]):
        if who.external_actions is None:
            raise HTTPException(503, "External action bridge is not configured")
        return who.external_actions.get(action_id).to_dict()

    @app.post("/api/external-actions/{action_id}/approve")
    def approve_external(
        action_id: str, body: Approval, who: Annotated[Principal, Depends(principal)]
    ):
        if who.external_actions is None:
            raise HTTPException(503, "External action bridge is not configured")
        return who.external_actions.approve(action_id, body.nonce).to_dict()

    @app.post("/api/external-actions/{action_id}/reject")
    def reject_external(action_id: str, who: Annotated[Principal, Depends(principal)]):
        if who.external_actions is None:
            raise HTTPException(503, "External action bridge is not configured")
        return who.external_actions.reject(action_id).to_dict()

    @app.post("/api/external-actions/{action_id}/cancel")
    def cancel_external(action_id: str, who: Annotated[Principal, Depends(principal)]):
        if who.external_actions is None:
            raise HTTPException(503, "External action bridge is not configured")
        return who.external_actions.cancel(action_id).to_dict()


def create_app(
    operator: Operator,
    access_token: str,
    *,
    model_factory=ScriptedModel,
    mode="scripted",
    public_origin=None,
    allowance_status=None,
) -> FastAPI:
    """Local single-owner app:one bearer credential,one operator,no cookies."""
    if len(access_token) < 32:
        raise ValueError("Access token must contain at least 32 random characters")
    public_origin = _origin(public_origin)
    app = _base_app(public_origin=public_origin, sessions=None)

    external = ExternalActionBridge(operator.store)

    def owner(request: Request):
        expected = "Bearer " + access_token
        actual = request.headers.get("authorization", "")
        if not secrets.compare_digest(actual.encode(), expected.encode()):
            raise HTTPException(401, "Owner credential required")
        return Principal(operator, model_factory, mode, allowance_status, external_actions=external)

    _task_routes(app, owner)
    _external_action_routes(app, owner)

    @app.get("/api/config", dependencies=[Depends(owner)])
    def configuration():
        return {"mode": mode, "live_inference": mode == "meta", "https_origin": public_origin}

    return app


def create_hosted_app(
    scoped,
    sessions,
    grant=None,
    *,
    actor_factory=None,
    public_origin=None,
):
    """Hosted app:shared access code,per-visitor stores,optional visitor-owned key.

    Without an attached visitor key every task runs the labelled offline
    reference,so browsing the workflow costs nothing. A configured grant plus a
    visitor key enables real Meta work paid by that visitor's own account.
    """
    public_origin = _origin(public_origin)

    def build_actor(store, key_source):
        if actor_factory is not None:
            return actor_factory(store, key_source)
        from remote_operator.meta import MetaModel

        return MetaModel("", grant, store, key_source=key_source)

    app = _base_app(public_origin=public_origin, sessions=sessions)

    def visitor(request: Request):
        token = request.cookies.get(SESSION_COOKIE, "")
        try:
            subject = sessions.subject(token)
        except SessionUnavailable:
            raise HTTPException(401, "Visitor session required") from None
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get(SESSION_HEADER) != "1"
        ):
            raise HTTPException(403, "Explicit session request header required")
        operator = scoped.for_subject(subject)
        external = ExternalActionBridge(operator.store)
        if grant is not None and sessions.has_key(token, subject):
            factory = lambda: build_actor(operator.store, lambda: sessions.key_for(token, subject))
            return Principal(
                operator,
                factory,
                "meta",
                lambda: grant.budget_status(operator.store),
                True,
                True,
                external,
            )
        return Principal(operator, ScriptedModel, "scripted", None, True, False, external)

    _task_routes(app, visitor)
    _external_action_routes(app, visitor)

    @app.get("/api/config")
    def configuration(request: Request):
        token = request.cookies.get(SESSION_COOKIE, "")
        try:
            subject = sessions.subject(token)
            attached = grant is not None and sessions.has_key(token, subject)
        except SessionUnavailable:
            return {
                "hosted": True,
                "authenticated": False,
                "mode": "scripted",
                "live_inference": False,
                "https_origin": public_origin,
                "session_key_attached": False,
                "model_route_configured": grant is not None,
            }
        return {
            "hosted": True,
            "authenticated": True,
            "mode": "meta" if attached else "scripted",
            "live_inference": attached,
            "https_origin": public_origin,
            "session_key_attached": attached,
            "model_route_configured": grant is not None,
        }

    @app.post("/api/session")
    def open_session(body: AccessCode, response: Response):
        try:
            token = sessions.open(body.code)
        except AccessRefused:
            raise HTTPException(403, "Access code refused") from None
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(sessions.lifetime),
            httponly=True,
            samesite="strict",
            secure=public_origin is not None,
            path="/",
        )
        return {"authenticated": True, "mode": "scripted", "expires_in": sessions.lifetime}

    @app.delete("/api/session")
    def close_session(
        request: Request, response: Response, who: Annotated[Principal, Depends(visitor)]
    ):
        sessions.close(request.cookies.get(SESSION_COOKIE, ""))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"closed": True}

    @app.post("/api/session/key")
    def attach_key(
        body: ProviderKey, request: Request, who: Annotated[Principal, Depends(visitor)]
    ):
        if grant is None:
            raise HTTPException(409, "Hosted model route is not configured")
        try:
            sessions.attach_key(request.cookies.get(SESSION_COOKIE, ""), body.api_key)
        except SessionCapacity:
            raise HTTPException(429, "Demo is holding too many attached keys; try later") from None
        return {"key_attached": True, "mode": "meta"}

    @app.delete("/api/session/key")
    def detach_key(request: Request, who: Annotated[Principal, Depends(visitor)]):
        token = request.cookies.get(SESSION_COOKIE, "")
        sessions.drop_key(token, sessions.subject(token))
        return {"key_attached": False, "mode": "scripted"}

    return app
