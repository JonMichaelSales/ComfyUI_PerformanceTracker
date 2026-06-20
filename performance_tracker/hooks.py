from __future__ import annotations

import copy
import logging
from typing import Any

from server import PromptServer

from .database import capture_prompt, load_prompt_payload, persist_run
from .extractor import extract_graph_factors, summarize_history

LOGGER = logging.getLogger("ComfyUI-Performance-Tracker")
_INSTALLED = False


def install_hooks() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    server = PromptServer.instance
    server.add_on_prompt_handler(_capture_on_prompt)
    _wrap_task_done(server)


def _capture_on_prompt(json_data: dict[str, Any]) -> dict[str, Any]:
    try:
        prompt = json_data.get("prompt")
        prompt_id = json_data.get("prompt_id")
        extra_data = copy.deepcopy(json_data.get("extra_data") or {})
        if isinstance(prompt, dict) and prompt_id:
            capture_prompt(str(prompt_id), prompt, extra_data)
    except Exception:
        LOGGER.exception("Failed to capture prompt payload")
    return json_data


def _wrap_task_done(server: PromptServer) -> None:
    queue = server.prompt_queue
    if getattr(queue, "_performance_tracker_wrapped", False):
        return
    original = queue.task_done

    def wrapped_task_done(item_id, history_result, status, process_item=None):
        prompt_id = None
        prompt = None
        extra_data = {}
        try:
            running_item = queue.currently_running.get(item_id)
            if running_item:
                prompt_tuple = running_item
                prompt_id = str(prompt_tuple[1])
                prompt = prompt_tuple[2]
                extra_data = prompt_tuple[3] if len(prompt_tuple) > 3 and isinstance(prompt_tuple[3], dict) else {}
                if prompt_id and isinstance(prompt, dict):
                    capture_prompt(prompt_id, prompt, extra_data)
        except Exception:
            LOGGER.exception("Failed to snapshot running prompt before task_done")

        result = original(item_id, history_result, status, process_item=process_item)

        try:
            if prompt_id is None:
                return result
            payload = load_prompt_payload(prompt_id)
            if payload is None and isinstance(prompt, dict):
                payload = {"prompt": prompt, "extra_data": extra_data, "create_time": extra_data.get("create_time")}
            if payload is None:
                return result
            factors = extract_graph_factors(payload["prompt"])
            summary = summarize_history(history_result or {}, status)
            persist_run(prompt_id, payload, factors, summary)
        except Exception:
            LOGGER.exception("Failed to persist completed performance record")
        return result

    queue.task_done = wrapped_task_done
    queue._performance_tracker_wrapped = True
