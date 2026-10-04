"""Health probes, OpenAPI and Prometheus exposition."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


class TestHealth:
    async def test_liveness_never_touches_a_dependency(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

    async def test_readiness_reports_the_database(self, client: AsyncClient) -> None:
        response = await client.get("/health/ready")
        assert response.status_code in {200, 503}
        body = response.json()
        assert body["checks"]["database"] is True
        if response.status_code == 200:
            assert body["status"] == "ready"
            assert body["environment"] == "testing"

    async def test_readiness_reports_503_when_redis_is_missing(
        self,
        client: AsyncClient,
        redis_available: bool,
    ) -> None:
        if redis_available:
            pytest.skip("Redis is running in this environment")
        response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["status"] == "degraded"

    async def test_root_advertises_the_docs(self, client: AsyncClient) -> None:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.json()["docs"] == "/docs"


class TestOpenApi:
    async def test_schema_is_generated(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        assert schema["info"]["title"] == "TaskHub API"
        assert schema["info"]["version"] == "1.0.0"

    async def test_every_domain_is_documented(self, client: AsyncClient) -> None:
        paths = (await client.get("/openapi.json")).json()["paths"]
        for expected in (
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/auth/refresh",
            "/api/v1/projects",
            "/api/v1/tasks",
            "/api/v1/stats/overview",
            "/api/v1/users",
            "/api/v1/tags",
            "/health/live",
            "/health/ready",
        ):
            assert expected in paths, f"{expected} is missing from the OpenAPI schema"

    async def test_security_scheme_is_advertised(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        schemes = schema["components"].get("securitySchemes", {})
        assert any(value.get("scheme") == "bearer" for value in schemes.values())

    async def test_pagination_is_documented_with_bounds(self, client: AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        params = {
            param["name"]: param
            for param in schema["paths"]["/api/v1/tasks"]["get"]["parameters"]
            if param["in"] == "query"
        }
        assert {"page", "size"} <= set(params)
        # OpenAPI 3.1 keeps the bounds inside the parameter schema.
        assert params["size"]["schema"]["maximum"] == 100
        assert params["page"]["schema"]["minimum"] == 1

    async def test_swagger_ui_is_served(self, client: AsyncClient) -> None:
        assert (await client.get("/docs")).status_code == 200
        assert (await client.get("/redoc")).status_code == 200


class TestMetrics:
    async def test_exposition_includes_the_http_collectors(self, client: AsyncClient) -> None:
        await client.get("/health/live")
        payload = (await client.get("/metrics")).text
        assert "http_requests_total" in payload
        assert "http_request_duration_seconds_bucket" in payload

    async def test_metrics_are_not_recorded_for_the_scrape_itself(
        self, client: AsyncClient
    ) -> None:
        before = (await client.get("/metrics")).text
        assert 'path="/metrics"' not in before

    async def test_metrics_use_route_templates_not_raw_paths(self, logged_in: AsyncClient) -> None:
        """Labelling by raw URL would create one series per resource id."""
        await logged_in.get("/api/v1/projects")
        response = await logged_in.get("/metrics")
        payload = response.text
        assert 'path="/api/v1/projects"' in payload, [
            line for line in payload.splitlines() if line.startswith("http_requests_total")
        ]


class TestMiddleware:
    async def test_a_request_id_is_always_returned(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")
        assert response.headers["X-Request-ID"]

    async def test_inbound_request_id_is_preserved(self, client: AsyncClient) -> None:
        response = await client.get("/health/live", headers={"X-Request-ID": "fixed-id"})
        assert response.headers["X-Request-ID"] == "fixed-id"

    async def test_request_id_appears_in_the_error_body(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/projects")
        assert response.status_code == 401
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]

    async def test_response_time_header_is_present(self, client: AsyncClient) -> None:
        response = await client.get("/health/live")
        assert float(response.headers["X-Response-Time-Ms"]) >= 0

    async def test_large_responses_are_compressed(self, logged_in: AsyncClient) -> None:
        response = await logged_in.get(
            "/api/v1/projects",
            headers={"Accept-Encoding": "gzip"},
        )
        assert response.status_code == 200
        assert response.headers.get("content-encoding") == "gzip"
