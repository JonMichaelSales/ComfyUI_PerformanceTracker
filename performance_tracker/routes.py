from __future__ import annotations

from typing import Any

from aiohttp import web
from server import PromptServer

from .database import (
    clear_history,
    get_run,
    get_run_by_output,
    get_run_output_assets,
    get_settings,
    list_runs,
    save_settings,
    set_run_exclusion,
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

    @routes.get("/performance-tracker/health")
    async def performance_health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "name": "ComfyUI-Performance-Tracker"})

    @routes.get("/performance-tracker/runs")
    async def performance_runs(request: web.Request) -> web.Response:
        limit = _bounded_int(request.query.get("limit"), 50, 1, 200)
        offset = _bounded_int(request.query.get("offset"), 0, 0, 10_000_000)
        payload = list_runs(
            limit=limit,
            offset=offset,
            model=_clean(request.query.get("model")),
            status=_clean(request.query.get("status")),
            lora=_clean(request.query.get("lora")),
            workflow_hash=_clean(request.query.get("workflow_hash")),
            include_excluded=request.query.get("include_excluded", "1") != "0",
        )
        return web.json_response(payload)

    @routes.get("/performance-tracker/runs/{prompt_id}")
    async def performance_run_detail(request: web.Request) -> web.Response:
        run = get_run(request.match_info["prompt_id"])
        if run is None:
            return _json_error(404, "NOT_FOUND", "Run not found.")
        return web.json_response(run)

    @routes.get("/performance-tracker/runs/{prompt_id}/assets")
    async def performance_run_assets(request: web.Request) -> web.Response:
        payload = get_run_output_assets(request.match_info["prompt_id"])
        if payload is None:
            return _json_error(404, "NOT_FOUND", "Run not found.")
        return web.json_response(payload)

    @routes.get("/performance-tracker/assets/by-output")
    async def performance_asset_by_output(request: web.Request) -> web.Response:
        filename = _clean(request.query.get("filename"))
        if not filename:
            return _json_error(400, "BAD_OUTPUT", "filename is required.")
        run = get_run_by_output(
            filename=filename,
            subfolder=_clean(request.query.get("subfolder")) or "",
            file_type=_clean(request.query.get("type")) or "output",
        )
        if run is None:
            return _json_error(404, "NOT_FOUND", "No run found for that output.")
        return web.json_response({"run": run})

    @routes.post("/performance-tracker/runs/{prompt_id}/exclusion")
    async def performance_run_exclusion(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        excluded = bool(body.get("excluded"))
        note = _clean(body.get("note")) if isinstance(body, dict) else None
        updated = set_run_exclusion(request.match_info["prompt_id"], excluded, note)
        if not updated:
            return _json_error(404, "NOT_FOUND", "Run not found.")
        return web.json_response({"updated": True, "excluded_from_stats": excluded})

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

    @routes.get("/performance-tracker/settings")
    async def performance_settings(request: web.Request) -> web.Response:
        return web.json_response(get_settings())

    @routes.post("/performance-tracker/settings")
    async def performance_save_settings(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return _json_error(400, "BAD_JSON", "Expected a JSON settings payload.")
        if not isinstance(body, dict):
            return _json_error(400, "BAD_JSON", "Expected a JSON object.")
        aliases = body.get("aliases")
        if aliases is not None and not isinstance(aliases, list):
            return _json_error(400, "BAD_ALIASES", "Aliases must be a list.")
        settings = body.get("settings")
        if settings is not None and not isinstance(settings, dict):
            return _json_error(400, "BAD_SETTINGS", "Settings must be an object.")
        return web.json_response(save_settings(settings or {}, aliases or []))

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



