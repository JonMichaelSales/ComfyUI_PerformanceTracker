from __future__ import annotations

from typing import Any

from aiohttp import web
from server import PromptServer

from .database import (
    clear_history,
    get_run,
    list_runs,
    stats_loras,
    stats_models,
    stats_overview,
    stats_workflows,
)

_ROUTES_REGISTERED = False


def register_routes() -> None:
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return
    _ROUTES_REGISTERED = True
    routes = PromptServer.instance.routes

    @routes.get("/performance-tracker/runs")
    async def performance_runs(request: web.Request) -> web.Response:
        limit = _bounded_int(request.query.get("limit"), 50, 1, 200)
        offset = _bounded_int(request.query.get("offset"), 0, 0, 10_000_000)
        payload = list_runs(
            limit=limit,
            offset=offset,
            model=_clean(request.query.get("model")),
            status=_clean(request.query.get("status")),
        )
        return web.json_response(payload)

    @routes.get("/performance-tracker/runs/{prompt_id}")
    async def performance_run_detail(request: web.Request) -> web.Response:
        run = get_run(request.match_info["prompt_id"])
        if run is None:
            return _json_error(404, "NOT_FOUND", "Run not found.")
        return web.json_response(run)

    @routes.get("/performance-tracker/stats/overview")
    async def performance_overview(request: web.Request) -> web.Response:
        return web.json_response(stats_overview())

    @routes.get("/performance-tracker/stats/models")
    async def performance_model_stats(request: web.Request) -> web.Response:
        limit = _bounded_int(request.query.get("limit"), 50, 1, 200)
        return web.json_response({"models": stats_models(limit)})

    @routes.get("/performance-tracker/stats/loras")
    async def performance_lora_stats(request: web.Request) -> web.Response:
        limit = _bounded_int(request.query.get("limit"), 50, 1, 200)
        return web.json_response({"loras": stats_loras(limit)})

    @routes.get("/performance-tracker/stats/workflows")
    async def performance_workflow_stats(request: web.Request) -> web.Response:
        limit = _bounded_int(request.query.get("limit"), 50, 1, 200)
        return web.json_response({"workflows": stats_workflows(limit)})

    @routes.post("/performance-tracker/admin/reindex")
    async def performance_reindex(request: web.Request) -> web.Response:
        # V1 stores normalized rows at write time. This endpoint exists as a stable API
        # for future migrations and currently acts as a no-op health check.
        return web.json_response({"reindexed": False, "message": "No reindex needed for schema version 1."})

    @routes.post("/performance-tracker/admin/clear")
    async def performance_clear(request: web.Request) -> web.Response:
        clear_history()
        return web.json_response({"cleared": True})


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        parsed = default
    return min(max(parsed, minimum), maximum)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _json_error(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message, "details": details or {}}},
        status=status,
    )
