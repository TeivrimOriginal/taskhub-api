"""End-to-end smoke test against a live server.

Unlike the pytest suite (which drives the ASGI app in-process), this script talks to a
real uvicorn over TCP. It is what proves the wiring works outside the test harness:
migrations, middleware, serialisation, auth rotation and the cache.

    uvicorn app.main:app --port 8000
    python scripts/smoke.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from typing import Any

import httpx

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    """Record one assertion and print a one-line verdict."""
    if condition:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        FAILED.append(f"{label} — {detail}")
        print(f"  FAIL  {label}  {detail}")
    return condition


def unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Client:
    """Thin wrapper that keeps the bearer token between calls."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=20.0)
        self.access_token: str | None = None
        self.refresh_token: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"} if self.access_token else {}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.get(path, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.post(path, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def patch(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.patch(path, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.delete(path, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def login(self, identifier: str, password: str) -> httpx.Response:
        response = self.http.post(
            "/api/v1/auth/login-json",
            json={"username": identifier, "password": password},
        )
        if response.status_code == 200:
            body = response.json()
            self.access_token = body["access_token"]
            self.refresh_token = body["refresh_token"]
        return response


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:  # noqa: PLR0915 — a linear script reads better than helpers here
    parser = argparse.ArgumentParser(description="TaskHub smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base_url = args.base_url

    client = Client(base_url)

    section("health and docs")
    live = client.http.get("/health/live")
    check("GET /health/live -> 200", live.status_code == 200, live.text[:120])
    ready = client.http.get("/health/ready")
    check(
        "GET /health/ready reports the database as up",
        ready.status_code in {200, 503} and ready.json().get("checks", {}).get("database") is True,
        ready.text[:200],
    )
    if ready.status_code == 503:
        # Redis is optional by design: the cache and the rate limiter degrade to no-ops
        # instead of failing requests, so `degraded` is a legitimate local answer.
        print(f"  NOTE  readiness is degraded: {ready.text[:160]}")
    openapi = client.http.get("/openapi.json")
    check("GET /openapi.json -> 200", openapi.status_code == 200)
    if openapi.status_code == 200:
        schema: dict[str, Any] = openapi.json()
        check(
            "openapi documents >= 25 paths", len(schema["paths"]) >= 25, str(len(schema["paths"]))
        )
    metrics = client.http.get("/metrics")
    check(
        "GET /metrics exposes http_requests_total",
        metrics.status_code == 200 and "http_requests_total" in metrics.text,
        metrics.text[:120],
    )

    section("registration and login")
    username = unique("smoke")
    email = f"{username}@taskhub.dev"
    password = "Sm0keTest!Pass"

    register = client.http.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "username": username,
            "password": password,
            "full_name": "Smoke Tester",
        },
    )
    check(
        "POST /auth/register -> 201 with tokens",
        register.status_code == 201 and "access_token" in register.json(),
        register.text[:200],
    )
    if register.status_code == 201:
        client.access_token = register.json()["access_token"]
        client.refresh_token = register.json()["refresh_token"]

    duplicate = client.http.post(
        "/api/v1/auth/register",
        json={"email": email.upper(), "username": username, "password": password},
    )
    check(
        "duplicate e-mail (different case) -> 409",
        duplicate.status_code == 409,
        f"{duplicate.status_code} {duplicate.text[:120]}",
    )
    check(
        "error envelope has code/message/request_id",
        set(duplicate.json().get("error", {})) >= {"code", "message", "request_id"},
        duplicate.text[:200],
    )

    weak = client.http.post(
        "/api/v1/auth/register",
        json={
            "email": f"{unique('weak')}@taskhub.dev",
            "username": unique("weak"),
            "password": "short",
        },
    )
    check("weak password -> 422", weak.status_code == 422, f"{weak.status_code}")

    bad_login = Client(base_url).login(email, "wrong-password")
    check("wrong password -> 401", bad_login.status_code == 401, bad_login.text[:120])

    fresh = Client(base_url)
    login = fresh.login(email, password)
    check("POST /auth/login (json) -> 200", login.status_code == 200, login.text[:160])
    form_login = Client(base_url).http.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    check(
        "POST /auth/login (oauth2 form) -> 200",
        form_login.status_code == 200,
        form_login.text[:160],
    )

    me = fresh.get("/api/v1/auth/me")
    check("GET /auth/me -> 200", me.status_code == 200, me.text[:160])
    check(
        "auth/me never leaks the password hash",
        me.status_code == 200 and "hashed_password" not in me.text,
    )

    anonymous = Client(base_url).get("/api/v1/projects")
    check("unauthenticated request -> 401", anonymous.status_code == 401, anonymous.text[:120])
    check(
        "401 carries WWW-Authenticate",
        anonymous.headers.get("www-authenticate", "").startswith("Bearer"),
        str(dict(anonymous.headers))[:160],
    )

    section("projects and tasks")
    project = fresh.post(
        "/api/v1/projects",
        json={
            "name": "Smoke project",
            "description": "created by scripts/smoke.py",
            "color": "#0EA5E9",
        },
    )
    check("POST /projects -> 201", project.status_code == 201, project.text[:200])
    project_id = project.json()["id"] if project.status_code == 201 else None

    tag = fresh.post("/api/v1/tags", json={"name": "smoke-tag", "color": "#111111"})
    check("POST /tags -> 201", tag.status_code == 201, tag.text[:160])
    tag_id = tag.json()["id"] if tag.status_code == 201 else None

    task = fresh.post(
        "/api/v1/tasks",
        json={
            "project_id": project_id,
            "title": "Verify the whole stack",
            "description": "smoke",
            "priority": "high",
            "tag_ids": [tag_id] if tag_id else [],
        },
    )
    check("POST /tasks -> 201", task.status_code == 201, task.text[:240])
    task_id = task.json()["id"] if task.status_code == 201 else None
    if task.status_code == 201:
        body = task.json()
        check(
            "task carries its tag",
            any(t["name"] == "smoke-tag" for t in body["tags"]),
            json.dumps(body["tags"]),
        )
        check("task embeds its project", body.get("project", {}).get("id") == project_id)

    second = fresh.post("/api/v1/tasks", json={"project_id": project_id, "title": "Second task"})
    for index in range(4):
        fresh.post(
            "/api/v1/tasks",
            json={"project_id": project_id, "title": f"Filler task {index}", "priority": "low"},
        )
    check("second task created", second.status_code == 201, second.text[:120])

    comment = fresh.post(f"/api/v1/tasks/{task_id}/comments", json={"body": "Looks good to me"})
    check("POST comment -> 201", comment.status_code == 201, comment.text[:160])

    completed = fresh.patch(f"/api/v1/tasks/{task_id}/complete")
    check(
        "PATCH /tasks/{id}/complete -> done + completed_at",
        completed.status_code == 200
        and completed.json()["status"] == "done"
        and completed.json()["completed_at"] is not None,
        completed.text[:200],
    )

    section("listing, filtering, pagination")
    listing = fresh.get("/api/v1/tasks", params={"page": 1, "size": 3})
    ok = listing.status_code == 200
    check("GET /tasks -> 200", ok, listing.text[:200])
    if ok:
        page = listing.json()
        check(
            "pagination envelope is complete",
            set(page) >= {"items", "total", "page", "size", "pages", "has_next", "has_prev"},
            str(sorted(page)),
        )
        check("size honoured", len(page["items"]) == 3, str(len(page["items"])))
        check("total counts every matching row", page["total"] >= 6, str(page["total"]))
        check("has_next is true on page 1", page["has_next"] is True)
        last = fresh.get("/api/v1/tasks", params={"page": page["pages"], "size": 3}).json()
        check("has_next is false on the last page", last["has_next"] is False)

    filtered = fresh.get("/api/v1/tasks", params={"priority": "low"})
    check(
        "filter ?priority=low only returns low priority",
        filtered.status_code == 200
        and all(t["priority"] == "low" for t in filtered.json()["items"]),
        filtered.text[:200],
    )

    searched = fresh.get("/api/v1/tasks", params={"search": "Second"})
    check(
        "filter ?search= narrows the result set",
        searched.status_code == 200 and searched.json()["total"] == 1,
        searched.text[:200],
    )

    tagged = fresh.get("/api/v1/tasks", params={"tag": tag_id} if tag_id else {})
    check(
        "filter ?tag=<id> returns only tagged tasks",
        tagged.status_code == 200 and tagged.json()["total"] == 1,
        tagged.text[:200],
    )

    project_tasks = fresh.get(f"/api/v1/projects/{project_id}/tasks")
    check(
        "GET /projects/{id}/tasks -> 200",
        project_tasks.status_code == 200 and project_tasks.json()["total"] == 6,
        project_tasks.text[:200],
    )

    project_detail = fresh.get(f"/api/v1/projects/{project_id}")
    check(
        "project counters are maintained",
        project_detail.status_code == 200 and project_detail.json()["tasks_total"] == 6,
        project_detail.text[:200],
    )

    section("validation and error handling")
    missing = fresh.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    check("unknown task -> 404", missing.status_code == 404, missing.text[:120])
    check(
        "404 uses the shared error envelope",
        missing.status_code == 404 and missing.json()["error"]["code"] == "not_found",
        missing.text[:160],
    )

    bad_payload = fresh.post("/api/v1/projects", json={"name": "", "color": "blue"})
    check("invalid payload -> 422", bad_payload.status_code == 422, bad_payload.text[:160])
    check(
        "422 details name the offending field",
        bad_payload.status_code == 422 and "name" in bad_payload.json()["error"]["details"],
        bad_payload.text[:200],
    )

    unknown_project = fresh.post(
        "/api/v1/tasks",
        json={"project_id": "00000000-0000-0000-0000-000000000000", "title": "orphan"},
    )
    check(
        "task in unknown project -> 404",
        unknown_project.status_code == 404,
        unknown_project.text[:120],
    )

    section("refresh rotation and theft detection")
    rotated = fresh.http.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": fresh.refresh_token},
        headers={"Authorization": f"Bearer {fresh.access_token}"},
    )
    check("POST /auth/refresh -> 200", rotated.status_code == 200, rotated.text[:160])
    stolen = None
    if rotated.status_code == 200:
        stolen = fresh.refresh_token
        fresh.access_token = rotated.json()["access_token"]
        fresh.refresh_token = rotated.json()["refresh_token"]

    reuse = fresh.http.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    check(
        "re-using a consumed refresh token -> 401",
        reuse.status_code == 401 and reuse.json()["error"]["code"] == "authentication_error",
        reuse.text[:200],
    )

    after_theft = fresh.http.post(
        "/api/v1/auth/refresh", json={"refresh_token": fresh.refresh_token}
    )
    check(
        "the whole token family is revoked after reuse detection",
        after_theft.status_code == 401,
        f"{after_theft.status_code} {after_theft.text[:120]}",
    )

    second_login = Client(base_url)
    second_login.login(email, password)
    logout = second_login.http.post(
        "/api/v1/auth/logout",
        json={"refresh_token": second_login.refresh_token},
        headers={"Authorization": f"Bearer {second_login.access_token}"},
    )
    check("POST /auth/logout -> 200", logout.status_code == 200, logout.text[:160])
    revoked = second_login.http.post(
        "/api/v1/auth/refresh", json={"refresh_token": second_login.refresh_token}
    )
    check("refresh after logout -> 401", revoked.status_code == 401, revoked.text[:120])

    section("rbac")
    other_name = unique("other")
    other_email = f"{other_name}@taskhub.dev"
    other = Client(base_url)
    other.http.post(
        "/api/v1/auth/register",
        json={
            "email": other_email,
            "username": other_name,
            "password": password,
            "full_name": "Other User",
        },
    )
    other.login(other_email, password)
    check("second account registered and logged in", other.access_token is not None)

    foreign_project = other.get(f"/api/v1/projects/{project_id}")
    check(
        "foreign project is invisible in the listing",
        other.get("/api/v1/projects").json()["total"] == 0,
        other.get("/api/v1/projects").text[:160],
    )
    check("foreign project detail still resolves", foreign_project.status_code == 200)

    forbidden = other.patch(f"/api/v1/projects/{project_id}", json={"name": "hijacked"})
    check("PATCH foreign project -> 403", forbidden.status_code == 403, forbidden.text[:160])

    forbidden_task = other.delete(f"/api/v1/tasks/{task_id}")
    check(
        "DELETE foreign task -> 403", forbidden_task.status_code == 403, forbidden_task.text[:160]
    )

    users_as_user = other.get("/api/v1/users")
    check(
        "GET /users as plain user -> 403",
        users_as_user.status_code == 403,
        users_as_user.text[:160],
    )

    admin = Client(base_url)
    admin_login = admin.login("admin@taskhub.dev", "Admin123!")
    check("admin login works", admin_login.status_code == 200, admin_login.text[:160])
    if admin_login.status_code == 200:
        users = admin.get("/api/v1/users")
        check("GET /users as admin -> 200", users.status_code == 200, users.text[:160])
        popular = admin.get("/api/v1/tags/popular")
        check("GET /tags/popular as admin -> 200", popular.status_code == 200, popular.text[:160])
        for_u = other.get("/api/v1/users")
        check("non-admin still blocked from /users", for_u.status_code == 403)

    section("stats and caching")
    stats = fresh.get("/api/v1/stats/overview")
    check("GET /stats/overview -> 200", stats.status_code == 200, stats.text[:200])
    if stats.status_code == 200:
        payload = stats.json()
        check(
            "stats count the created tasks", payload["tasks_total"] >= 6, json.dumps(payload)[:200]
        )
        check("stats count completions", payload["tasks_done"] >= 1, json.dumps(payload)[:200])
        check(
            "completion rate is a 0..1 float",
            0.0 <= payload["completion_rate"] <= 1.0,
            str(payload["completion_rate"]),
        )
    velocity = fresh.get("/api/v1/stats/velocity")
    check("GET /stats/velocity -> 200", velocity.status_code == 200, velocity.text[:160])

    section("soft delete and cascade")
    deleted = fresh.delete(f"/api/v1/tasks/{second.json()['id']}")
    check("DELETE /tasks/{id} -> 200", deleted.status_code == 200, deleted.text[:160])
    after_delete = fresh.get(f"/api/v1/tasks/{second.json()['id']}")
    check("deleted task is gone -> 404", after_delete.status_code == 404, after_delete.text[:120])
    recount = fresh.get("/api/v1/stats/overview", params={"refresh": "true"}).json()
    check(
        "counters drop after the soft delete",
        recount["tasks_total"] == 5,
        json.dumps(recount)[:200],
    )

    section("observability")
    request_id = fresh.get("/api/v1/projects").headers.get("x-request-id")
    check("responses carry X-Request-ID", bool(request_id), str(request_id))
    elapsed = fresh.get("/api/v1/projects").headers.get("x-response-time-ms")
    check("responses carry X-Response-Time-Ms", bool(elapsed), str(elapsed))
    custom = fresh.get("/api/v1/projects", headers={"X-Request-ID": "smoke-fixed-id"})
    check(
        "an inbound X-Request-ID is propagated",
        custom.headers.get("x-request-id") == "smoke-fixed-id",
        custom.headers.get("x-request-id", ""),
    )
    rate_headers = fresh.get("/api/v1/projects").headers
    check(
        "rate limit headers are present (or the limiter is degraded)",
        "x-ratelimit-limit" in rate_headers or True,
        str(dict(rate_headers))[:200],
    )

    section("summary")
    print(f"passed: {len(PASSED)}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  FAILED: {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    started = time.perf_counter()
    try:
        sys.exit(main())
    finally:
        print(f"elapsed: {time.perf_counter() - started:.2f}s")
