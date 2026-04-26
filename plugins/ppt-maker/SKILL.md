---
name: ppt-maker
description: Guided PPT generation for editable PowerPoint decks, table-driven reports, and enterprise-template presentations.
---

# ppt-maker Skill Card

Use this plugin when the user asks to create, revise, audit, or export a PPT
deck. Prefer it over ad-hoc slide scripts when the task needs guided
requirements, source files, table insights, enterprise templates, or a
downloadable editable PPTX.

## Tool Order

1. `ppt_start_project`
2. `ppt_ingest_sources` or `ppt_ingest_table` when files/data are provided
3. `ppt_generate_outline`
4. `ppt_confirm_outline`
5. `ppt_generate_design`
6. `ppt_confirm_design`
7. `ppt_generate_deck`
8. `ppt_audit`
9. `ppt_export`

For enterprise templates, call `ppt_upload_template` and
`ppt_diagnose_template` before generating the design spec.

