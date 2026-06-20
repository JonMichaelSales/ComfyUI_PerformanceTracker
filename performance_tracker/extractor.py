from __future__ import annotations

import hashlib
import json
from typing import Any

MODEL_KEYS = ("ckpt_name", "model_name", "unet_name", "diffusion_model_name")
LORA_KEYS = ("lora_name",)
DIMENSION_KEYS = ("width", "height", "batch_size")
SAMPLER_KEYS = ("sampler_name", "scheduler", "steps", "cfg", "seed", "noise_seed", "denoise")


def workflow_hash(prompt: dict[str, Any]) -> str:
    normalized = json.dumps(prompt, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def extract_graph_factors(prompt: dict[str, Any]) -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    loras: list[dict[str, Any]] = []
    samplers: list[dict[str, Any]] = []
    dimensions: list[dict[str, Any]] = []
    output_classes: list[str] = []

    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}

        for key in MODEL_KEYS:
            if inputs.get(key):
                models.append({
                    "node_id": str(node_id),
                    "class_type": class_type,
                    "kind": key,
                    "name": str(inputs[key]),
                })

        for key in LORA_KEYS:
            if inputs.get(key):
                loras.append({
                    "node_id": str(node_id),
                    "class_type": class_type,
                    "name": str(inputs[key]),
                    "strength_model": _float_or_none(inputs.get("strength_model")),
                    "strength_clip": _float_or_none(inputs.get("strength_clip")),
                })

        if _looks_like_sampler(class_type, inputs):
            samplers.append({
                "node_id": str(node_id),
                "class_type": class_type,
                "sampler_name": _string_or_none(inputs.get("sampler_name")),
                "scheduler": _string_or_none(inputs.get("scheduler")),
                "steps": _int_or_none(inputs.get("steps")),
                "cfg": _float_or_none(inputs.get("cfg")),
                "seed": _string_or_none(inputs.get("seed", inputs.get("noise_seed"))),
                "denoise": _float_or_none(inputs.get("denoise")),
            })

        if any(key in inputs for key in DIMENSION_KEYS):
            dimensions.append({
                "node_id": str(node_id),
                "class_type": class_type,
                "width": _int_or_none(inputs.get("width")),
                "height": _int_or_none(inputs.get("height")),
                "batch_size": _int_or_none(inputs.get("batch_size")),
            })

        if class_type.lower().startswith("save") or class_type in {"PreviewImage", "SaveImage"}:
            output_classes.append(class_type)

    return {
        "workflow_hash": workflow_hash(prompt),
        "total_node_count": len(prompt),
        "models": _dedupe_dicts(models),
        "loras": _dedupe_dicts(loras),
        "samplers": _dedupe_dicts(samplers),
        "dimensions": dimensions,
        "output_classes": sorted(set(output_classes)),
        "primary_model": models[0]["name"] if models else None,
        "primary_sampler": samplers[0]["sampler_name"] if samplers else None,
        "primary_steps": samplers[0]["steps"] if samplers else None,
        "primary_cfg": samplers[0]["cfg"] if samplers else None,
        "primary_seed": samplers[0]["seed"] if samplers else None,
        "primary_width": _first_present(dimensions, "width"),
        "primary_height": _first_present(dimensions, "height"),
        "primary_batch_size": _first_present(dimensions, "batch_size"),
    }


def summarize_history(history_result: dict[str, Any], status: Any) -> dict[str, Any]:
    status_dict = status._asdict() if hasattr(status, "_asdict") else (status or {})
    messages = status_dict.get("messages") or []
    start_ts = None
    end_ts = None
    cached_nodes: list[str] = []
    error_summary = None
    interrupted = False

    for event_name, event_data in _iter_messages(messages):
        if event_name == "execution_start":
            start_ts = event_data.get("timestamp")
        elif event_name == "execution_cached":
            cached_nodes = [str(n) for n in event_data.get("nodes", [])]
        elif event_name in {"execution_success", "execution_error", "execution_interrupted"}:
            end_ts = event_data.get("timestamp")
            if event_name == "execution_error":
                error_summary = {
                    "node_id": event_data.get("node_id"),
                    "node_type": event_data.get("node_type"),
                    "exception_type": event_data.get("exception_type"),
                    "exception_message": event_data.get("exception_message"),
                }
            elif event_name == "execution_interrupted":
                interrupted = True

    outputs = history_result.get("outputs") or {}
    output_count = 0
    output_files: list[dict[str, Any]] = []
    for node_id, node_output in outputs.items():
        if not isinstance(node_output, dict):
            continue
        for kind, items in node_output.items():
            if not isinstance(items, list):
                continue
            output_count += len(items)
            for item in items:
                if isinstance(item, dict):
                    output_files.append({
                        "node_id": str(node_id),
                        "kind": str(kind),
                        "filename": item.get("filename"),
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                    })

    return {
        "status_str": status_dict.get("status_str"),
        "completed": bool(status_dict.get("completed")),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "duration_ms": (end_ts - start_ts) if isinstance(start_ts, int) and isinstance(end_ts, int) else None,
        "cached_nodes": cached_nodes,
        "cached_node_count": len(cached_nodes),
        "error_summary": error_summary,
        "interrupted": interrupted,
        "output_count": output_count,
        "output_files": output_files,
        "messages_json": messages,
    }


def _iter_messages(messages: list[Any]):
    for entry in messages:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2 and isinstance(entry[1], dict):
            yield entry[0], entry[1]


def _looks_like_sampler(class_type: str, inputs: dict[str, Any]) -> bool:
    if "sampler" in class_type.lower():
        return True
    return any(key in inputs for key in SAMPLER_KEYS) and "steps" in inputs


def _dedupe_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for item in items:
        marker = json.dumps(item, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


def _first_present(items: list[dict[str, Any]], key: str) -> Any:
    for item in items:
        if item.get(key) is not None:
            return item[key]
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
