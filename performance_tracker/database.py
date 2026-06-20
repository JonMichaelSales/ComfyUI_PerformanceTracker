from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import folder_paths

EXTENSION_NAME = "ComfyUI-Performance-Tracker"
SCHEMA_VERSION = 1
DEFAULT_SETTINGS = {
    "use_friendly_model_names": True,
    "hide_file_extensions": True,
    "stats_limit": 50,
}
MODEL_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin")


def data_dir() -> Path:
    path = Path(folder_paths.get_user_directory()) / EXTENSION_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "performance.sqlite"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path()), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_payloads (
            prompt_id TEXT PRIMARY KEY,
            prompt_json TEXT NOT NULL,
            extra_data_json TEXT,
            create_time INTEGER,
            captured_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            prompt_id TEXT PRIMARY KEY,
            status TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            create_ts INTEGER,
            start_ts INTEGER,
            end_ts INTEGER,
            duration_ms INTEGER,
            cached_node_count INTEGER NOT NULL DEFAULT 0,
            executed_node_count INTEGER NOT NULL DEFAULT 0,
            total_node_count INTEGER NOT NULL DEFAULT 0,
            output_count INTEGER NOT NULL DEFAULT 0,
            workflow_hash TEXT,
            primary_model TEXT,
            primary_sampler TEXT,
            primary_steps INTEGER,
            primary_cfg REAL,
            primary_seed TEXT,
            primary_width INTEGER,
            primary_height INTEGER,
            primary_batch_size INTEGER,
            error_summary_json TEXT,
            excluded_from_stats INTEGER NOT NULL DEFAULT 0,
            exclusion_note TEXT,
            factors_json TEXT,
            messages_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_models (
            prompt_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            class_type TEXT,
            kind TEXT,
            name TEXT NOT NULL,
            FOREIGN KEY(prompt_id) REFERENCES runs(prompt_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_loras (
            prompt_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            class_type TEXT,
            name TEXT NOT NULL,
            strength_model REAL,
            strength_clip REAL,
            FOREIGN KEY(prompt_id) REFERENCES runs(prompt_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_samplers (
            prompt_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            class_type TEXT,
            sampler_name TEXT,
            scheduler TEXT,
            steps INTEGER,
            cfg REAL,
            seed TEXT,
            denoise REAL,
            FOREIGN KEY(prompt_id) REFERENCES runs(prompt_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_outputs (
            prompt_id TEXT NOT NULL,
            node_id TEXT,
            kind TEXT,
            filename TEXT,
            subfolder TEXT,
            type TEXT,
            FOREIGN KEY(prompt_id) REFERENCES runs(prompt_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_aliases (
            model_name TEXT PRIMARY KEY,
            friendly_name TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_duration ON runs(duration_ms)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_model ON runs(primary_model)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_hash ON runs(workflow_hash)")
    _ensure_column(conn, "runs", "excluded_from_stats", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "runs", "exclusion_note", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_excluded ON runs(excluded_from_stats)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_models_name ON run_models(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_loras_name ON run_loras(name)")
    if conn.execute("SELECT COUNT(*) AS count FROM schema_info").fetchone()["count"] == 0:
        conn.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def capture_prompt(prompt_id: str, prompt: dict[str, Any], extra_data: dict[str, Any] | None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO prompt_payloads(prompt_id, prompt_json, extra_data_json, create_time, captured_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(prompt_id) DO UPDATE SET
                prompt_json = excluded.prompt_json,
                extra_data_json = excluded.extra_data_json,
                create_time = excluded.create_time,
                captured_at = excluded.captured_at
            """,
            (
                prompt_id,
                _dump(prompt),
                _dump(extra_data or {}),
                (extra_data or {}).get("create_time"),
                time.time(),
            ),
        )


def load_prompt_payload(prompt_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM prompt_payloads WHERE prompt_id = ?", (prompt_id,)).fetchone()
    if not row:
        return None
    return {
        "prompt": _load(row["prompt_json"]) or {},
        "extra_data": _load(row["extra_data_json"]) or {},
        "create_time": row["create_time"],
    }


def persist_run(prompt_id: str, payload: dict[str, Any], factors: dict[str, Any], summary: dict[str, Any]) -> None:
    now = time.time()
    cached_count = int(summary["cached_node_count"])
    total_nodes = int(factors["total_node_count"])
    executed_count = max(total_nodes - cached_count, 0)
    with connect() as conn:
        conn.execute("DELETE FROM run_models WHERE prompt_id = ?", (prompt_id,))
        conn.execute("DELETE FROM run_loras WHERE prompt_id = ?", (prompt_id,))
        conn.execute("DELETE FROM run_samplers WHERE prompt_id = ?", (prompt_id,))
        conn.execute("DELETE FROM run_outputs WHERE prompt_id = ?", (prompt_id,))
        conn.execute(
            """
            INSERT INTO runs (
                prompt_id, status, completed, create_ts, start_ts, end_ts, duration_ms,
                cached_node_count, executed_node_count, total_node_count, output_count,
                workflow_hash, primary_model, primary_sampler, primary_steps, primary_cfg,
                primary_seed, primary_width, primary_height, primary_batch_size,
                error_summary_json, factors_json, messages_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(prompt_id) DO UPDATE SET
                status = excluded.status,
                completed = excluded.completed,
                create_ts = excluded.create_ts,
                start_ts = excluded.start_ts,
                end_ts = excluded.end_ts,
                duration_ms = excluded.duration_ms,
                cached_node_count = excluded.cached_node_count,
                executed_node_count = excluded.executed_node_count,
                total_node_count = excluded.total_node_count,
                output_count = excluded.output_count,
                workflow_hash = excluded.workflow_hash,
                primary_model = excluded.primary_model,
                primary_sampler = excluded.primary_sampler,
                primary_steps = excluded.primary_steps,
                primary_cfg = excluded.primary_cfg,
                primary_seed = excluded.primary_seed,
                primary_width = excluded.primary_width,
                primary_height = excluded.primary_height,
                primary_batch_size = excluded.primary_batch_size,
                error_summary_json = excluded.error_summary_json,
                factors_json = excluded.factors_json,
                messages_json = excluded.messages_json,
                updated_at = excluded.updated_at
            """,
            (
                prompt_id,
                summary["status_str"],
                1 if summary["completed"] else 0,
                payload.get("create_time"),
                summary["start_ts"],
                summary["end_ts"],
                summary["duration_ms"],
                cached_count,
                executed_count,
                total_nodes,
                summary["output_count"],
                factors["workflow_hash"],
                factors["primary_model"],
                factors["primary_sampler"],
                factors["primary_steps"],
                factors["primary_cfg"],
                factors["primary_seed"],
                factors["primary_width"],
                factors["primary_height"],
                factors["primary_batch_size"],
                _dump(summary["error_summary"]),
                _dump(factors),
                _dump(summary["messages_json"]),
                now,
                now,
            ),
        )
        conn.executemany(
            "INSERT INTO run_models(prompt_id, node_id, class_type, kind, name) VALUES(?, ?, ?, ?, ?)",
            [(prompt_id, m["node_id"], m["class_type"], m["kind"], m["name"]) for m in factors["models"]],
        )
        conn.executemany(
            """
            INSERT INTO run_loras(prompt_id, node_id, class_type, name, strength_model, strength_clip)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            [(prompt_id, l["node_id"], l["class_type"], l["name"], l["strength_model"], l["strength_clip"]) for l in factors["loras"]],
        )
        conn.executemany(
            """
            INSERT INTO run_samplers(prompt_id, node_id, class_type, sampler_name, scheduler, steps, cfg, seed, denoise)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    prompt_id,
                    s["node_id"],
                    s["class_type"],
                    s["sampler_name"],
                    s["scheduler"],
                    s["steps"],
                    s["cfg"],
                    s["seed"],
                    s["denoise"],
                )
                for s in factors["samplers"]
            ],
        )
        conn.executemany(
            "INSERT INTO run_outputs(prompt_id, node_id, kind, filename, subfolder, type) VALUES(?, ?, ?, ?, ?, ?)",
            [
                (prompt_id, o["node_id"], o["kind"], o["filename"], o["subfolder"], o["type"])
                for o in summary["output_files"]
            ],
        )


def list_runs(
    limit: int,
    offset: int,
    model: str | None = None,
    status: str | None = None,
    lora: str | None = None,
    workflow_hash: str | None = None,
    include_excluded: bool = True,
) -> dict[str, Any]:
    where = ["1 = 1"]
    params: list[Any] = []
    if model:
        where.append("primary_model = ?")
        params.append(model)
    if status:
        where.append("status = ?")
        params.append(status)
    if workflow_hash:
        where.append("workflow_hash = ?")
        params.append(workflow_hash)
    if lora:
        where.append("EXISTS (SELECT 1 FROM run_loras l WHERE l.prompt_id = runs.prompt_id AND l.name = ?)")
        params.append(lora)
    if not include_excluded:
        where.append("excluded_from_stats = 0")
    clause = " AND ".join(where)
    with connect() as conn:
        display_config = _display_config(conn)
        total = conn.execute(f"SELECT COUNT(*) AS count FROM runs WHERE {clause}", params).fetchone()["count"]
        rows = conn.execute(
            f"SELECT * FROM runs WHERE {clause} ORDER BY COALESCE(end_ts, updated_at * 1000) DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {
        "runs": [_run_summary(dict(row), display_config) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


def set_run_exclusion(prompt_id: str, excluded: bool, note: str | None = None) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE runs SET excluded_from_stats = ?, exclusion_note = ?, updated_at = ? WHERE prompt_id = ?",
            (1 if excluded else 0, note, time.time(), prompt_id),
        )
        return cur.rowcount > 0

def get_run(prompt_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        display_config = _display_config(conn)
        row = conn.execute("SELECT * FROM runs WHERE prompt_id = ?", (prompt_id,)).fetchone()
        if not row:
            return None
        outputs = conn.execute("SELECT * FROM run_outputs WHERE prompt_id = ?", (prompt_id,)).fetchall()
    detail = _run_summary(dict(row), display_config)
    detail["factors"] = _load(row["factors_json"]) or {}
    detail["messages"] = _load(row["messages_json"]) or []
    detail["error_summary"] = _load(row["error_summary_json"])
    detail["outputs"] = [dict(o) for o in outputs]
    return detail


def get_run_by_output(filename: str, subfolder: str = "", file_type: str = "output") -> dict[str, Any] | None:
    with connect() as conn:
        display_config = _display_config(conn)
        row = conn.execute(
            """
            SELECT r.*
            FROM runs r
            JOIN run_outputs o ON o.prompt_id = r.prompt_id
            WHERE o.filename = ? AND COALESCE(o.subfolder, '') = ? AND COALESCE(o.type, 'output') = ?
            ORDER BY COALESCE(r.end_ts, r.updated_at * 1000) DESC
            LIMIT 1
            """,
            (filename, subfolder or "", file_type or "output"),
        ).fetchone()
        if not row:
            return None
        outputs = conn.execute("SELECT * FROM run_outputs WHERE prompt_id = ?", (row["prompt_id"],)).fetchall()
    detail = _run_summary(dict(row), display_config)
    detail["outputs"] = [dict(o) for o in outputs]
    return detail


def get_run_output_assets(prompt_id: str) -> dict[str, Any] | None:
    run = get_run(prompt_id)
    if not run:
        return None
    return {
        "prompt_id": prompt_id,
        "outputs": [
            {
                **output,
                "view_url": _build_view_url(output.get("filename"), output.get("subfolder") or "", output.get("type") or "output"),
            }
            for output in run.get("outputs", [])
            if output.get("filename")
        ],
    }


def stats_overview() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total_runs,
                   AVG(duration_ms) AS avg_duration_ms,
                   AVG(CASE WHEN total_node_count > 0 THEN CAST(cached_node_count AS REAL) / total_node_count ELSE 0 END) AS avg_cache_rate,
                   MIN(duration_ms) AS fastest_ms,
                   MAX(duration_ms) AS slowest_ms
            FROM runs WHERE duration_ms IS NOT NULL AND excluded_from_stats = 0
            """
        ).fetchone()
    return dict(row)


def stats_models(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        display_config = _display_config(conn)
        rows = conn.execute(
            """
            SELECT COALESCE(primary_model, '(unknown)') AS model,
                   SUM(CASE WHEN excluded_from_stats = 0 THEN 1 ELSE 0 END) AS run_count,
                   SUM(CASE WHEN excluded_from_stats = 1 THEN 1 ELSE 0 END) AS excluded_count,
                   AVG(CASE WHEN excluded_from_stats = 0 THEN duration_ms END) AS avg_duration_ms,
                   MIN(CASE WHEN excluded_from_stats = 0 THEN duration_ms END) AS fastest_ms,
                   MAX(CASE WHEN excluded_from_stats = 0 THEN duration_ms END) AS slowest_ms,
                   AVG(CASE WHEN excluded_from_stats = 0 THEN primary_steps END) AS avg_steps,
                   AVG(CASE WHEN excluded_from_stats = 0 THEN primary_width * primary_height * COALESCE(primary_batch_size, 1) END) AS avg_pixels
            FROM runs
            WHERE duration_ms IS NOT NULL
            GROUP BY COALESCE(primary_model, '(unknown)')
            ORDER BY avg_duration_ms IS NULL ASC, avg_duration_ms DESC, run_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_add_model_display(dict(row), "model", "model_display", display_config) for row in rows]

def stats_loras(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT l.name AS lora,
                   SUM(CASE WHEN r.excluded_from_stats = 0 THEN 1 ELSE 0 END) AS run_count,
                   SUM(CASE WHEN r.excluded_from_stats = 1 THEN 1 ELSE 0 END) AS excluded_count,
                   AVG(CASE WHEN r.excluded_from_stats = 0 THEN r.duration_ms END) AS avg_duration_ms
            FROM run_loras l
            JOIN runs r ON r.prompt_id = l.prompt_id
            WHERE r.duration_ms IS NOT NULL
            GROUP BY l.name
            ORDER BY avg_duration_ms IS NULL ASC, avg_duration_ms DESC, run_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]

def stats_workflows(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        display_config = _display_config(conn)
        rows = conn.execute(
            """
            SELECT workflow_hash,
                   SUM(CASE WHEN excluded_from_stats = 0 THEN 1 ELSE 0 END) AS run_count,
                   SUM(CASE WHEN excluded_from_stats = 1 THEN 1 ELSE 0 END) AS excluded_count,
                   AVG(CASE WHEN excluded_from_stats = 0 THEN duration_ms END) AS avg_duration_ms,
                   MAX(CASE WHEN excluded_from_stats = 0 THEN duration_ms END) AS slowest_ms,
                   MAX(primary_model) AS sample_model
            FROM runs
            WHERE duration_ms IS NOT NULL
            GROUP BY workflow_hash
            ORDER BY avg_duration_ms IS NULL ASC, avg_duration_ms DESC, run_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_add_model_display(dict(row), "sample_model", "sample_model_display", display_config) for row in rows]

def clear_history() -> None:
    with connect() as conn:
        for table in ("run_outputs", "run_samplers", "run_loras", "run_models", "runs", "prompt_payloads"):
            conn.execute(f"DELETE FROM {table}")


def get_settings() -> dict[str, Any]:
    with connect() as conn:
        settings = _settings_dict(conn)
        aliases = [
            {"model_name": row["model_name"], "friendly_name": row["friendly_name"]}
            for row in conn.execute("SELECT model_name, friendly_name FROM model_aliases ORDER BY lower(model_name)").fetchall()
        ]
        models = _model_candidates(conn)
    return {"settings": settings, "aliases": aliases, "models": models}


def save_settings(settings: dict[str, Any] | None, aliases: list[dict[str, Any]] | None) -> dict[str, Any]:
    clean_settings = _coerce_settings(settings or {})
    clean_aliases: dict[str, str] = {}
    for alias in aliases or []:
        if not isinstance(alias, dict):
            continue
        model_name = str(alias.get("model_name") or "").strip()
        friendly_name = str(alias.get("friendly_name") or "").strip()
        if model_name and friendly_name:
            clean_aliases[model_name] = friendly_name

    with connect() as conn:
        for key, value in clean_settings.items():
            conn.execute(
                """
                INSERT INTO settings(key, value_json) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (key, _dump(value)),
            )
        conn.execute("DELETE FROM model_aliases")
        now = time.time()
        conn.executemany(
            "INSERT INTO model_aliases(model_name, friendly_name, updated_at) VALUES(?, ?, ?)",
            [(model_name, friendly_name, now) for model_name, friendly_name in sorted(clean_aliases.items(), key=lambda item: item[0].lower())],
        )
    return get_settings()


def _run_summary(row: dict[str, Any], display_config: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = {
        "prompt_id": row["prompt_id"],
        "status": row["status"],
        "completed": bool(row["completed"]),
        "create_ts": row["create_ts"],
        "start_ts": row["start_ts"],
        "end_ts": row["end_ts"],
        "duration_ms": row["duration_ms"],
        "cached_node_count": row["cached_node_count"],
        "executed_node_count": row["executed_node_count"],
        "total_node_count": row["total_node_count"],
        "output_count": row["output_count"],
        "workflow_hash": row["workflow_hash"],
        "primary_model": row["primary_model"],
        "primary_sampler": row["primary_sampler"],
        "primary_steps": row["primary_steps"],
        "primary_cfg": row["primary_cfg"],
        "primary_seed": row["primary_seed"],
        "primary_width": row["primary_width"],
        "primary_height": row["primary_height"],
        "primary_batch_size": row["primary_batch_size"],
        "excluded_from_stats": bool(row.get("excluded_from_stats", 0)),
        "exclusion_note": row.get("exclusion_note"),
    }
    return _add_model_display(summary, "primary_model", "primary_model_display", display_config)


def _settings_dict(conn: sqlite3.Connection) -> dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    rows = conn.execute("SELECT key, value_json FROM settings").fetchall()
    for row in rows:
        if row["key"] in DEFAULT_SETTINGS:
            settings[row["key"]] = _load(row["value_json"])
    return _coerce_settings(settings)


def _coerce_settings(settings: dict[str, Any]) -> dict[str, Any]:
    coerced = dict(DEFAULT_SETTINGS)
    if "use_friendly_model_names" in settings:
        coerced["use_friendly_model_names"] = bool(settings["use_friendly_model_names"])
    if "hide_file_extensions" in settings:
        coerced["hide_file_extensions"] = bool(settings["hide_file_extensions"])
    if "stats_limit" in settings:
        try:
            coerced["stats_limit"] = min(max(int(settings["stats_limit"]), 10), 200)
        except (TypeError, ValueError):
            coerced["stats_limit"] = DEFAULT_SETTINGS["stats_limit"]
    return coerced


def _display_config(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "settings": _settings_dict(conn),
        "aliases": {
            row["model_name"]: row["friendly_name"]
            for row in conn.execute("SELECT model_name, friendly_name FROM model_aliases").fetchall()
        },
    }


def _add_model_display(row: dict[str, Any], source_key: str, target_key: str, display_config: dict[str, Any] | None) -> dict[str, Any]:
    row[target_key] = _display_model_name(row.get(source_key), display_config)
    return row


def _display_model_name(model_name: str | None, display_config: dict[str, Any] | None) -> str | None:
    if not model_name:
        return model_name
    settings = (display_config or {}).get("settings") or DEFAULT_SETTINGS
    aliases = (display_config or {}).get("aliases") or {}
    if settings.get("use_friendly_model_names", True) and model_name in aliases:
        return aliases[model_name]
    if settings.get("hide_file_extensions", True):
        lower_name = model_name.lower()
        for extension in MODEL_EXTENSIONS:
            if lower_name.endswith(extension):
                return model_name[: -len(extension)]
    return model_name


def _model_candidates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name FROM (
            SELECT primary_model AS name FROM runs WHERE primary_model IS NOT NULL AND primary_model != ''
            UNION
            SELECT name FROM run_models WHERE name IS NOT NULL AND name != ''
            UNION
            SELECT model_name AS name FROM model_aliases WHERE model_name IS NOT NULL AND model_name != ''
        )
        ORDER BY lower(name)
        """
    ).fetchall()
    return [row["name"] for row in rows]


def _build_view_url(filename: str | None, subfolder: str, file_type: str) -> str | None:
    if not filename:
        return None
    from urllib.parse import quote

    url = f"/view?type={quote(file_type)}&filename={quote(filename)}"
    if subfolder:
        url += f"&subfolder={quote(subfolder)}"
    return url


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value



