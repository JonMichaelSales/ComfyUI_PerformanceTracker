# ComfyUI Performance Tracker

ComfyUI Performance Tracker records completed generation runs in a local SQLite database and adds a ComfyUI panel for reviewing model averages, recent runs, workflow timings, cache impact, and extracted generation factors.

V1 is intentionally workflow-level tracking. It does not patch core node execution internals, so model timing is a derived association from each prompt graph rather than exact per-model execution profiling.

## Features

- Records completed generation runs without blocking generation on tracker failures.
- Stores local history in `ComfyUI-Performance-Tracker/performance.sqlite` under the ComfyUI user directory.
- Extracts common factors from prompt graphs:
  - checkpoints/models from `ckpt_name`, `model_name`, `unet_name`, and `diffusion_model_name`
  - LoRAs and strengths
  - sampler, scheduler, steps, CFG, seed, denoise
  - width, height, batch size
  - output filenames when Comfy history provides them
- Adds a Performance panel with tabs for Models, Recent Runs, Workflows, and LoRAs.
- Includes admin clear-history action with confirmation in the UI.

## Installation

Clone or copy this folder into your ComfyUI `custom_nodes` directory:

```powershell
git clone https://github.com/CHANGE-ME/ComfyUI-Performance-Tracker.git
```

Restart ComfyUI. The extension has no external Python dependencies.

## Notes

- Existing generations are not backfilled in V1 because ComfyUI history does not always include enough timing context for older runs.
- The database keeps unlimited local history by default.
- Clearing history only deletes tracker records. It does not delete generated images or workflows.

## Publishing

Before publishing to the Comfy Registry, replace the placeholder repository URL, `PublisherId`, and icon URL in `pyproject.toml`.

Run the usual registry validation and publish flow:

```powershell
comfy node validate
comfy node publish
```
