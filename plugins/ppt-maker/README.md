# PPT Maker

Guided presentation generation for editable PPTX decks, table-driven reports,
and enterprise templates.

This plugin follows the same self-contained UI/runtime pattern as
`plugins/avatar-studio`: all frontend assets live under `ui/dist/`, helpers are
vendored under `ppt_maker_inline/`, and project data is stored under
`api.get_data_dir()/ppt-maker/`.

## MVP Modes

- `topic_to_deck`: generate a deck from a topic and guided requirements.
- `files_to_deck`: generate from documents, Markdown, URLs, and notes.
- `outline_to_deck`: turn an existing outline into a deck.
- `table_to_deck`: generate chart/table report decks from CSV/XLSX data.
- `template_deck`: adapt generation to an enterprise PPTX template.
- `revise_deck`: revise an existing project or a single slide.

## Smoke Test

1. Load the plugin.
2. Open the UI and check the Health panel.
3. Call `ppt_list_projects`.
4. Confirm the response states that project storage is ready after Phase 1.

