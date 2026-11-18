# Agent Log

## [2026-11-18 20:07] Pack output directory isolation

- **Problem:** When an AI title and SEO pack were generated, all companion files (video export, transcript, cuts report, and SEO pack) were placed flat in `kvm_output/`, making it difficult to locate and organize files belonging to specific runs.
- **Solution:** Implemented `unique_pack_dir` in `titles.py` and updated `pipeline.apply_title` so that when a pack is generated, all corresponding output files are contained within their own subdirectory named after the slug (e.g. `kvm_output/terminal-basics-for-ai-master-commands/`). Also updated `edits.find_edited` to search subdirectories for struck transcripts and added comprehensive unit and integration tests.
