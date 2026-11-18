# Agent Log

## [2026-11-18 21:15] Pack directory CLI flag, config toggle and plugin update

- **Problem:** Users needed the ability to choose whether pack exports are isolated in dedicated subfolders or written flat to the root output folder, and the plugin needed support for this option.
- **Solution:** Added `--pack-dir` and `--no-pack-dir` CLI flags to `cli.py`, added `pack_dir_auto()` and `pack_dir` field to `pipeline.Job` reading the `KVM_PACK_DIR` environment/.env toggle, updated `pipeline.apply_title` to respect this setting, updated the Claude Code plugin skill (`clean-recording/SKILL.md`) and manifest files, updated `.env.example`, and added unit and integration tests.

## [2026-11-18 20:07] Pack output directory isolation

- **Problem:** When an AI title and SEO pack were generated, all companion files (video export, transcript, cuts report, and SEO pack) were placed flat in `kvm_output/`, making it difficult to locate and organize files belonging to specific runs.
- **Solution:** Implemented `unique_pack_dir` in `titles.py` and updated `pipeline.apply_title` so that when a pack is generated, all corresponding output files are contained within their own subdirectory named after the slug (e.g. `kvm_output/terminal-basics-for-ai-master-commands/`). Also updated `edits.find_edited` to search subdirectories for struck transcripts and added comprehensive unit and integration tests.
