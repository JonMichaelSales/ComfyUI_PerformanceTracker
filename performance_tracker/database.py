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
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_duration ON runs(duration_ms)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_model ON runs(primary_model)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_runs_hash ON runs(workflow_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_models_name ON run_models(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_loras_name ON run_loras(name)")
    if conn.execute("SELECT COUNT(*) AS count FROM schema_info").fetchone()["count"] == 0:
        conn.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))


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


def list_runs(limit: int, offset: int, model: str | None = None, status: str | None = None) -> dict[str, Any]:
    where = ["1 = 1"]
    params: list[Any] = []
    if model:
        where.append("primary_model = ?")
        params.append(model)
    if status:
        where.append("status = ?")
        params.append(status)
    clause = " AND ".join(where)
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS count FROM runs WHERE {clause}", params).fetchone()["count"]
        rows = conn.execute(
            f"SELECT * FROM runs WHERE {clause} ORDER BY COALESCE(end_ts, updated_at * 1000) DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {"runs": [_run_summary(dict(row)) for row in rows], "total": total, "limit": limit, "offset": offset, "has_more": offset + len(rows) < total}


def get_run(prompt_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE prompt_id = ?", (prompt_id,)).fetchone()
        if not row:
            return None
        outputs = conn.execute("SELECT * FROM run_outputs WHERE prompt_id = ?", (prompt_id,)).fetchall()
    detail = _run_summary(dict(row))
    detail["factors"] = _load(row["factors_json"]) or {}
    detail["messages"] = _load(row["messages_json"]) or []
    detail["error_summary"] = _load(row["error_summary_json"])
    detail["outputs"] = [dict(o) for o in outputs]
    return detail


def stats_overview() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total_runs,
                   AVG(duration_ms) AS avg_duration_ms,
                   AVG(CASE WHEN total_node_count > 0 THEN CAST(cached_node_count AS REAL) / total_node_count ELSE 0 END) AS avg_cache_rate,
                   MIN(duration_ms) AS fastest_ms,
                   MAX(duration_ms) AS slowest_ms
            FROM runs WHERE duration_ms IS NOT NULL
            """
        ).fetchone()
    return dict(row)


def stats_models(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(primary_model, '(unknown)') AS model,
                   COUNT(*) AS run_count,
                   AVG(duration_ms) AS avg_duration_ms,
                   MIN(duration_ms) AS fastest_ms,
                   MAX(duration_ms) AS slowest_ms,
                   AVG(primary_steps) AS avg_steps,
                   AVG(primary_width * primary_height * COALESCE(primary_batch_size, 1)) AS avg_pixels
            FROM runs
            WHERE duration_ms IS NOT NULL
            GROUP BY COALESCE(primary_model, '(unknown)')
            ORDER BY avg_duration_ms DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def stats_loras(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT l.name AS lora,
                   COUNT(DISTINCT r.prompt_id) AS run_count,
                   AVG(r.duration_ms) AS avg_duration_ms
            FROM run_loras l
            JOIN runs r ON r.prompt_id = l.prompt_id
            WHERE r.duration_ms IS NOT NULL
            GROUP BY l.name
            ORDER BY avg_duration_ms DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def stats_workflows(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT workflow_hash,
                   COUNT(*) AS run_count,
                   AVG(duration_ms) AS avg_duration_ms,
                   MAX(duration_ms) AS slowest_ms,
                   MAX(primary_model) AS sample_model
            FROM runs
            WHERE duration_ms IS NOT NULL
            GROUP BY workflow_hash
            ORDER BY avg_duration_ms DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def clear_history() -> None:
    with connect() as conn:
        for table in ("run_outputs", "run_samplers", "run_loras", "run_models", "runs", "prompt_payloads"):
            conn.execute(f"DELETE FROM {table}")


def _run_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
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
    }


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
