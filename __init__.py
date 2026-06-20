"""ComfyUI Generation Performance Tracker extension."""

from .performance_tracker.hooks import install_hooks
from .performance_tracker.routes import register_routes

WEB_DIRECTORY = "./js"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

register_routes()
install_hooks()

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
