# fin-pulse · 财经脉动

> Finance news radar for OpenAkita. Three canonical modes —
> **daily_brief** (morning / noon / evening digest), **hot_radar**
> (keyword-triggered IM alerts), and **ask_news** (host Brain agent
> tools) — over eight first-party finance sources plus optional
> NewsNow aggregation.

---

## 1. Feature summary

| Mode | What it does | Entry points |
|------|--------------|--------------|
| `daily_brief` | Aggregate the last N hours, rank via AI score, render a markdown + HTML digest, push via host IM gateway. | `POST /digest/run`, `fin_pulse_create`, `on_schedule` hook |
| `hot_radar` | Match recent articles against keyword rules (`+must / !exclude / @alias`), fire IM dispatch with per-target cooldown. | `POST /hot_radar/run`, `POST /radar/evaluate`, `on_schedule` hook |
| `ask_news` | Seven agent tools registered on `register_tools` so the host Brain can query the article/digest index directly from chat. | `fin_pulse_*` tools |

### Data sources (V1.0)

Eight first-party fetchers plus optional **NewsNow** for social/aggregator
augmentation. All fetchers share `BaseFetcher` + `NormalizedItem` and
dedupe on a canonical URL hash; cross-source re-sightings are tracked via
`raw.also_seen_from`.

| Source id | Description |
|-----------|-------------|
| `wallstreetcn`    | 华尔街见闻 7x24 / latest |
| `cls_telegraph`   | 财联社电报 |
| `stcn`            | 证券时报 |
| `pboc`            | 中国人民银行货币政策 |
| `stats_gov`       | 国家统计局 |
| `fed_fomc`        | 美联储 FOMC 日历 + 新闻 |
| `us_treasury`     | 美国财政部 press releases |
| `sec_edgar`       | SEC EDGAR latest filings |
| `newsnow` *(opt)* | NewsNow 公共服务或自建 |

---

## 2. Architecture

```
┌─────────── plugin.py ───────────┐
│ on_load / on_unload              │
│  ├── FastAPI router (read+write) │
│  ├── register_tools (7 tools)    │
│  └── on_schedule hook            │
└─────────────────────────────────┘
          │
          ├── finpulse_task_manager (aiosqlite · 4 tables + assets_bus)
          ├── finpulse_fetchers     (8 sources + rss + newsnow)
          ├── finpulse_ai           (extract_tags · score_batch · dedupe)
          ├── finpulse_frequency    (+must / !exclude / @alias DSL)
          ├── finpulse_report       (markdown + HTML renderer)
          ├── finpulse_notification (line-boundary splitter)
          ├── finpulse_dispatch     (thin wrapper over api.send_message)
          ├── finpulse_services     (shared query service, see §6)
          └── finpulse_errors       (9 error_kind classifier)
```

Every LLM call goes through the host `api.get_brain()` — we do **not**
ship an IM SDK, we call `api.send_message(channel, chat_id, content)`.
Scheduled tasks run on the host `TaskScheduler`; the plugin only
registers a match predicate for `fin-pulse:` prefixed tasks.

---

## 3. Install & load

```bash
cd D:/OpenAkita/plugins/fin-pulse
# the 5-asset UI bundle ships vendored; no build step required
```

Restart the OpenAkita host and confirm in Plugin Manager that
`fin-pulse` is Active with permissions `tools.register`,
`routes.register`, `hooks.basic`, `data.own`, `channel.send`,
`brain.access`, `config.read`, `config.write` granted.

---

## 4. Configure

Open the plugin UI → **Settings** tab, in order:

1. **Channels** — one or more IM adapters must be registered in the
   host gateway (Feishu / WeCom / DingTalk / Telegram / OneBot / Email).
   If the list is empty, the top banner (`oa-config-banner`) will
   link you back here.
2. **NewsNow (optional)** — the 3-stage wizard:
   - Step 1: Mode → `off` / `public` / `self_host`.
   - Step 2: API URL → `https://.../api/s` (public) or
     `http://127.0.0.1:4444/api/s` (self-host).
   - Step 3: Probe → clicks `POST /ingest/source/newsnow` and shows
     a green pill on success.
   The public service is author-funded — keep polling reasonable.
3. **Schedules** — cron-driven daily_brief (`morning` / `noon` /
   `evening`) or hot_radar triggers. See §6 for cron examples.
4. **AI Brain** — fin-pulse reuses the host LLM factory; configure
   provider / model / temperature in OpenAkita → Settings → Models.

---

## 5. Daily usage

- **Today tab** — live article feed with source / window / min-score
  filters, copy-to-clipboard on each item, one-click `POST /ingest`.
- **Digests tab** — list of generated briefs; click for an iframe
  preview of the HTML blob, click **Resend** to fan out via the host
  gateway.
- **Radar tab** — keyword rule editor + **Dry run** button; saves to
  `config["radar_rules"]` so a scheduled hot_radar can read them.
- **Ask tab** — 7 agent tool cards with JSON samples and
  "copy natural-language prompt" buttons. Paste into the OpenAkita
  main chat window to invoke via Brain.

---

## 6. API reference

> Base path: `/api/plugins/fin-pulse`

| Method | Path | Body / Query | Notes |
|--------|------|-------------|-------|
| `GET` | `/health` | — | Plugin status + db_ready + data_dir |
| `GET` | `/modes` | — | `MODES` enum (fallback inline) |
| `GET` | `/config` | — | Redacts `*_api_key`, `*_webhook`, `*_token`, `*_secret` |
| `PUT` | `/config` | `{updates: {k: v}}` | Flat string map |
| `GET` | `/tasks` | `?mode&status&offset&limit` | Clamped `limit<=200` |
| `GET` | `/tasks/{id}` | — | 404 when absent |
| `POST` | `/tasks/{id}/cancel` | — | Idempotent |
| `POST` | `/ingest` | `{sources?, since_hours?}` | Creates an `ingest` task |
| `POST` | `/ingest/source/{source_id}` | — | Single-source probe |
| `GET` | `/articles` | `?q&source_id&since&min_score&sort&offset&limit` | |
| `GET` | `/articles/{id}` | — | Full raw_json |
| `POST` | `/digest/run` | `{session, since_hours?, top_k?, lang?}` | |
| `GET` | `/digests` | `?session&offset&limit` | Omits blobs |
| `GET` | `/digests/{id}` | — | Includes blobs |
| `GET` | `/digests/{id}/html` | — | `text/html` for iframing |
| `POST` | `/radar/evaluate` | `{rules_text, since_hours?, limit?, min_score?}` | Does not persist |
| `POST` | `/hot_radar/run` | `{rules_text, targets[], since_hours?, ...}` | Persists + dispatches |
| `POST` | `/dispatch/send` | `{channel, chat_id, content, ...}` | Thin wrapper over `api.send_message` |
| `GET` | `/available-channels` | — | Probes the host gateway |
| `GET` | `/schedules` | — | Only `fin-pulse:` prefixed tasks |
| `POST` | `/schedules` | `{mode, cron, channel, chat_id, ...}` | `mode=daily_brief|hot_radar` |
| `DELETE` | `/schedules/{id}` | — | Refuses non-`fin-pulse:` names |

### Agent tools (same envelope as REST, dispatched via Brain)

- `fin_pulse_create` — create + run an ingest/digest/radar task.
- `fin_pulse_status` — inspect a task by id.
- `fin_pulse_list` — paginate recent tasks (`limit` clamped to 200).
- `fin_pulse_cancel` — flip status to `canceled`.
- `fin_pulse_settings_get` / `fin_pulse_settings_set` — config CRUD.
- `fin_pulse_search_news` — keyword + source + `days` + `min_score`
  search over the articles index.

All integer args flow through a strict `_clamp(v, lo, hi, default)`
so a misbehaving Brain cannot ask for `limit=99999` and hit the DB.

### Cron examples

```json
{"mode": "daily_brief", "cron": "0 8 * * *",  "session": "morning",
 "channel": "feishu", "chat_id": "oc_xxx"}
{"mode": "daily_brief", "cron": "0 12 * * *", "session": "noon",
 "channel": "feishu", "chat_id": "oc_xxx"}
{"mode": "hot_radar",   "cron": "*/15 * * * *",
 "rules_text": "+美联储\n+降息\n!传闻",
 "channel": "feishu", "chat_id": "oc_xxx"}
```

---

## 7. Smoke test checklist

Follow this end-to-end to validate a fresh install:

1. Load plugin → `GET /health` returns `ok=true`.
2. Settings → Channels lists at least one adapter pill.
3. `POST /ingest` → Today tab shows a mixed 8-source feed within
   30s; the `oa-config-banner` disappears if channels are present.
4. NewsNow → select `public` → click **Probe** → green message with
   `items_count`.
5. `POST /digest/run` (morning) → Digests tab shows the new card;
   click **Preview** → iframe renders the HTML blob.
6. Settings → Schedules → **Create** → daily_brief at `0 9 * * *`
   to Feishu → 9:00 arrives → Feishu receives the brief (splitter
   handles long text automatically).
7. Radar tab → type `+美联储\n!广告` → **Dry run** → hits list
   populates → **Save rules**.
8. In OpenAkita main chat, ask *"今天美股有什么大事"* → Brain
   invokes `fin_pulse_search_news` and returns structured results.
9. Press `d` anywhere in the plugin UI → `data-theme` toggles
   between light and dark.

---

## 8. Development

```bash
# unit tests (212+ cases)
cd D:/OpenAkita/plugins/fin-pulse
python -m pytest tests/ -q

# just the UI hard contracts
python -m pytest tests/test_smoke.py -v
```

Critical dirs:

- `finpulse_*.py` — business modules (see §2).
- `ui/dist/index.html` — single-file React 18 app with a vendored
  5-asset bundle under `_assets/`.
- `tests/test_smoke.py::test_ui_hard_contracts` — enforces the
  avatar-studio UI Kit contract (tokens that must appear, tokens
  that must not).

---

## 9. Credits

- **TrendRadar** — keyword DSL, line-boundary splitter, MCP clamp
  helper inspiration.
- **Horizon** — AI scoring prompts, cross-source dedupe
  (simhash + title).
- **go-stock** — `canSendAlert` cooldown idea.
- **fed-statement-scraping / PbcCrawler** — central-bank calendar
  gating + PyExecJS fallback.
- **avatar-studio / footage-gate** — UI Kit + SQLite task manager
  contracts.

---

## 10. License

Same as OpenAkita — see `D:/OpenAkita/LICENSE`.
