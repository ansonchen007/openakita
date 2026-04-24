# Changelog

All notable changes to fin-pulse will be documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-04-24

First tagged release. Feature-complete against the
`fin-pulse_财经脉动插件_95c17c0d` plan.

### Added — Phase 1 (plugin skeleton)

- `plugin.json` manifest with eight permissions
  (`tools.register` / `routes.register` / `hooks.basic` / `data.own`
  / `channel.send` / `brain.access` / `config.read` / `config.write`)
  and seven declared `provides.tools`.
- `plugin.py` entry registering FastAPI router, agent tools,
  `on_schedule` match predicate, and a lazy async bootstrap.
- `finpulse_task_manager.py` — aiosqlite 4-table schema
  (`tasks` / `articles` / `digests` / `config`) plus a reserved
  `assets_bus` table for V2.0 cross-plugin handoff.
- `finpulse_models.py` — `MODES`, `SOURCE_DEFS`, `SESSIONS`,
  `DEFAULT_CRONS`, `SCORE_THRESHOLDS`.
- `finpulse_errors.py` — nine `error_kind` classifier
  (`network` / `timeout` / `auth` / `quota` / `rate_limit` /
  `dependency` / `moderation` / `not_found` / `unknown`) with ZH + EN hints.
- `ui/dist/index.html` single-page React 18 shell with the
  avatar-studio 5-asset bundle vendored under `_assets/` and
  the hard-contract tokens enforced by
  `tests/test_smoke.py::test_ui_hard_contracts`.

### Added — Phase 2 (ingestion)

- `finpulse_fetchers/base.py` — `NormalizedItem` dataclass,
  canonical URL hashing, `BaseFetcher` ABC, and fetcher registry.
- Eight first-party fetchers: `wallstreetcn`, `cls_telegraph`,
  `stcn`, `pboc`, `stats_gov`, `fed_fomc`, `us_treasury`,
  `sec_edgar`; plus `rss_fetcher` + optional `newsnow_fetcher`.
- `finpulse_pipeline.ingest` — orchestrates fetchers, deduplicates
  on canonical URL hash, tracks cross-source re-sightings via
  `raw.also_seen_from`, updates `source.{id}.last_ok` /
  `last_error` in config.

### Added — Phase 3 (AI filter)

- `finpulse_ai/filter.py` — two-stage filter
  (`extract_tags` → `score_batch`) reusing `api.get_brain()`;
  `batch_size=10` with per-item graceful-degradation.
- `finpulse_ai/dedupe.py` — canonical URL merge + simhash title
  dedupe (Horizon range), with optional LLM topic clustering gated
  by `dedupe.use_llm` (default off).
- Interest-file SHA256 cache: when `ai_interests` changes all
  `ai_score` rows are nulled so the next cycle re-scores.

### Added — Phase 4 (modes + dispatch + schedule)

- `finpulse_report/render.py` — `build_daily_brief()` that ranks +
  formats articles into markdown and HTML blobs with inline CSS
  mirroring the `avatar-studio` palette.
- `finpulse_pipeline.run_daily_brief` — persists the rendered
  digest into the `digests` table and marks the task succeeded.
- `finpulse_frequency.py` — `+must` / `!exclude` / `@alias`
  / `[GLOBAL_FILTER]` DSL compiler and matcher (TrendRadar port
  with the deepcopy + size-bound hardenings in §13.2 of the plan).
- `finpulse_pipeline.evaluate_radar` + `run_hot_radar` — radar
  evaluation over the articles index + per-target broadcast
  through `DispatchService`.
- `finpulse_notification/splitter.py` — line-boundary splitter
  with `base_header` prepend + oversize-line force split
  (fix for TrendRadar issue #1065 lost-headline bug).
- `finpulse_dispatch.py` — thin wrapper over `api.send_message`
  with per-key cooldown, content-hash dedupe, and inter-chunk
  pacing; `broadcast()` fans out to multiple `(channel, chat_id)`
  targets.
- `on_schedule` hook + `_is_finpulse_schedule` match predicate so
  the host `TaskScheduler` invokes fin-pulse only for tasks whose
  name starts with `fin-pulse:`.
- `/schedules` REST triad (`GET` / `POST` / `DELETE`) that
  creates `ScheduledTask.create_cron(silent=True)` so the host
  does not duplicate fin-pulse's own IM payloads.
- `/available-channels` REST route that enumerates the host
  gateway adapters with a graceful probe fallback.

### Added — Phase 5 (agent tools)

- `finpulse_services/query.py` — shared query service used by both
  the REST router and the seven agent tools. `_clamp` /
  `_clamp_float` mirror TrendRadar's guard so misbehaving LLM
  payloads cannot hand in `limit=99999`.
- `plugin._handle_tool` — async dispatch through
  `build_tool_dispatch()` so REST and tool surfaces stay lockstep.
- JSON serialisation helper with `default=str` fallback so
  exotic payloads never crash the Brain adapter.
- 26 new service tests covering clamp edge cases, redaction,
  settings CRUD, search filters, create-path validation for all
  three modes, and dispatch table coverage.

### Added — Phase 6 (UI polish + docs)

- 5-tab UI hydrated against the live REST surface:
  **Today** (source / window / min_score filters + copy +
  one-click ingest), **Digests** (generate + iframe preview +
  resend), **Radar** (rule editor + dry run + save), **Ask** (7
  tool cards with JSON samples + "copy natural-language prompt"),
  **Settings** (source health, channels with per-adapter test,
  schedule CRUD, NewsNow 3-stage wizard, LLM hint card).
- NewsNow 3-stage wizard (`off` / `public` / `self_host`) with
  public-service warning banner and self-host docker recipe.
- `tests/test_smoke.py::test_ui_tabs_are_hydrated` — regression
  guard that every hot-path REST call lives in `index.html`.
- Five docs at the plugin root: `README.md`, `SKILL.md`,
  `USER_TEST_CASES.md`, `CHANGELOG.md` (this file), `VALIDATION.md`.

### Notes

- Python 3.11+ required at runtime (host uses `StrEnum`).
- The FastMCP stdio entry was **explicitly deferred to V1.1**;
  V1.0 exposes tools through the host `register_tools` single
  track only.
- Test matrix: 213 passed + 4 skipped (intentional: live-network
  fetchers and optional `feedparser`).
