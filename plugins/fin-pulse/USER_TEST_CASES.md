# fin-pulse · 用户测试用例（V1.0）

> 3 modes × 3 cases = 9 + 1 smoke sweep. Run them in order after a
> fresh install to sign off the V1.0 milestone. Every case lists the
> trigger, the expected REST call, the UI observable, and the pass
> criteria.

Legend: **Trigger** – what the tester clicks / types. **Call** – the
HTTP request that should hit the plugin router. **Expect** – the
tangible outcome in the UI, SQLite row, or IM channel.

---

## A. `daily_brief` mode

### A1 · Generate a morning brief and preview it

- Trigger: Settings tab ready → switch to **Digests** tab →
  click the **早报 Morning** button.
- Call: `POST /digest/run` body
  `{"session": "morning", "since_hours": 12, "top_k": 20, "lang": "zh"}`.
- Expect:
  - A new `.card` appears in the Digests grid with session label
    `早报` and generated_at within the last 10s.
  - Clicking the card opens a modal with an iframe whose `src`
    ends in `/digests/{id}/html`; the iframe renders a formatted
    brief with header + per-article score bands.
  - SQLite: `SELECT * FROM digests ORDER BY generated_at DESC
    LIMIT 1` shows non-empty `markdown_blob` + `html_blob`.

### A2 · `top_k` is respected

- Trigger: POST to `/digest/run` directly via curl or the Ask
  tab with `{"session": "noon", "top_k": 3}`.
- Expect:
  - Response `digest.stats.total_selected == 3`.
  - The rendered markdown contains exactly 3 numbered items.

### A3 · Agent tool round-trip

- Trigger: In the OpenAkita main chat window, type *"fin-pulse 帮
  我生成今晚的财经晚报"*.
- Call: host Brain → `fin_pulse_create`
  `{mode: "daily_brief", session: "evening", ...}`.
- Expect:
  - Tool reply envelope has `ok=true`, `task_id` starts with
    `fp-`, and `digest.digest_id` is present.
  - The UI Digests tab shows the new card after a soft refresh.

---

## B. `hot_radar` mode

### B1 · Rule evaluation is preview-only

- Trigger: Radar tab → paste
  `+美联储\n+降息\n!传闻` → **Dry run**.
- Call: `POST /radar/evaluate`.
- Expect:
  - Hits list populates on the right; **no** task row is written
    (`SELECT count(*) FROM tasks WHERE mode = 'hot_radar'`
    unchanged).
  - The `radar.hits.count` caption shows the hit total + the
    selected since_hours window.

### B2 · Save rules persists across refresh

- Trigger: **Save rules** button after B1 → F5 the plugin.
- Call: `PUT /config` with `{updates: {radar_rules: "+美联储\n+降息\n!传闻"}}`.
- Expect:
  - On reload, the textarea preloads the exact same content.
  - `GET /config` returns the same `radar_rules` value (the key
    is not secretive, so **not** redacted).

### B3 · Scheduled radar fires per-target cooldown

- Pre-req: two IM adapters configured (e.g. `feishu`, `wework`).
- Trigger: Settings → Schedules → **Create schedule** with
  `mode=hot_radar`, `cron="*/2 * * * *"`, `channel=feishu`,
  `chat_id=oc_aaa`; repeat with `chat_id=oc_bbb`.
- Wait 2–3 minutes.
- Expect:
  - Both Feishu groups receive the same hit list **separately** —
    the broadcast uses a per-`chat_id` cooldown_key, so one group
    receiving a message does not block another.
  - On the next 2-minute tick, the same hits do **not** resend
    (cooldown catches them) unless `rules_text` has changed.

---

## C. `ask_news` mode

### C1 · Keyword search respects `days` clamp

- Trigger: Ask tab → copy the `fin_pulse_search_news` JSON sample
  → modify to `{"q": "美联储", "days": 99999, "limit": 99999}` →
  paste into the OpenAkita main chat as a tool call.
- Expect:
  - Response clamps `days` to 90, `limit` to 200, and the `window`
    field reflects that (`window.days=90`).
  - `ok=true`, `total` reflects the actual hit count in the
    articles index.

### C2 · `fin_pulse_status` on a canceled task

- Pre-req: create any task then cancel it via the UI.
- Trigger: call `fin_pulse_status` with the canceled `task_id`.
- Expect:
  - `ok=true`, `task.status == "canceled"`; the envelope includes
    the original `params` so the Brain can suggest a retry.

### C3 · Settings redaction is enforced end-to-end

- Trigger: Call `fin_pulse_settings_get` after setting
  `brain_api_key=sk-test-123` via `fin_pulse_settings_set`.
- Expect:
  - Response carries `config.brain_api_key == "***"` — **not**
    the real value. Non-secret keys (e.g. `ai_interests`) come
    back unredacted.
  - The plugin `/config` REST endpoint behaves identically.

---

## D. Smoke sweep

### D1 · UI hard contracts pass on a fresh clone

```powershell
cd D:/OpenAkita/plugins/fin-pulse
python -m pytest tests/test_smoke.py -v
```

Expect 6 passes in ~1s. Any failure means the `ui/dist/index.html`
has regressed against the avatar-studio contract (missing
`TAB_IDS`, missing `oa-config-banner`, or forbidden
`ReactDOM.render` call, etc.).

### D2 · Full suite is green

```powershell
python -m pytest tests/ -q
```

Expect **213 passed, 4 skipped** (or higher as new cases land).
Four intentional skips live in `test_fetchers_impl.py` / `test_dedupe.py`
for cases that need a live network or `feedparser` package.

---

## E. Known environment gotchas

- On Windows with Python 3.9, `tests/test_schedule.py` stubs
  `openakita.plugins.api` so `StrEnum` imports survive — no action
  needed, just don't be surprised by the stub inside that file.
- NewsNow **public** service is rate-limited; if B3 does not fire,
  check `/ingest/source/newsnow` status first.
- Feishu bots reject messages > 30 KB — the
  `finpulse_notification/splitter.py` chunks by line boundary at
  25 KB per chunk to stay safely below.
