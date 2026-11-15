# Brain: AI models

## How the robot thinks

- `robotd/brain.py` sends the conversation to an AI model and runs the tools the model asks for.
- The system prompt = `knowledge/personality.md` + rules + `knowledge/hardware.yaml`.
- A conversation resets after 10 idle minutes. Long-term memories stay (SQLite table `memories`).
- **What a new conversation starts with (2026-11-13):** ALL saved memories (newest first, up to about 9000 characters)
  and the last 6 journal lines. Before, only the 8 newest memories were loaded, so things told days ago (the grant
  research) were forgotten unless the robot thought of calling `recall`.
- **A conversation is kept small (2026-11-13, `compact_messages` in `robotd/brain.py`):** more than 10 owner turns ->
  the last 6 stay word for word and the older ones become one short recap block; only the 2 newest photos stay (older
  ones become a one-line note); tool results older than two turns are cut to 600 characters. Before, every old photo
  and web page was sent again on every turn (one day averaged 22,000 input tokens a call): slow, costly, and it made
  small models worse. The system prompt is never changed by this, so prompt caching keeps working.
- Every AI call is logged in the `llm_calls` table (model, time taken, tokens, ok, rating).

## Providers

| Provider | Status | Notes |
|---|---|---|
| OpenRouter | **works**, default | One key, hundreds of models. Key: `OPENROUTER_API_KEY` in `data/secrets.env`. |
| Anthropic (direct Claude API) | **no credit** | Key exists but the account has no credit. These models are marked unavailable. |
| Offline | always | Simple built-in answers, no AI. |

The owner uses Claude Opus through **Claude Code on the PC** (subscription), connected to the robot with MCP.
That is the "smart helper" path. It does not use the Anthropic API credit.

## Picking a model

- The active model is `brain.active` in `data/config.json`. The list is `brain.models`.
- The list contains the **variety pack**: 12 cheap, capable OpenRouter models, refreshed from the live catalog every 6 hours.
  Slots: DeepSeek, GLM Flash, Gemini Flash, Gemini Flash Lite, NVIDIA vision, Qwen VL, Llama vision, Mistral Small,
  GPT mini, Claude Haiku, Free pick, Free pick 2. The newest model that matches each slot is chosen automatically.
- Switch: screen Settings > AI brain (tap to cycle), web app AI tab, or `POST /api/brain/select {"key":"<key>"}`.
- Use any OpenRouter model: `POST /api/brain/use {"model_id":"provider/model-name"}`.
- Default and recommended: `anthropic/claude-haiku-5.5` via OpenRouter (cheap, good at tools, benchmark 86%).
- Models marked with vision can see photos. Models without vision cannot use `look` or `read_text` well.

## Prices

- Live prices come from `https://openrouter.ai/api/v1/models` (cached 10 minutes). Shown as $ per million tokens (input/output).
- `GET /api/models?sort=price` (also `context`, `company`, `newest`, `name`).
- Free models (`:free`) can be slow or rate limited.

## Benchmarks

- 7 tasks: persona (short spoken answers), reasoning, math, tool use, JSON output, vision counting, reading text (OCR).
- Run: `POST /api/bench/run {"models":["id1","id2"]}` or the web app AI tab. Results: `GET /api/bench`.
- It costs a little money per model. Ask the owner before benchmarking many models.

## The brain's tools (what the robot's AI can do)

| Tool | Does |
|---|---|
| `look` | Takes a photo and looks at it |
| `read_text` | Sharp photo + OCR to read text |
| `see_live` | Fast local vision: faces, smiles, motion, QR |
| `body_status` | Health, devices, tilt, last tests |
| `get_social_stats` | `hours`: how the Bluesky posts are doing: totals, each post, and what changed in the last N hours (`social.md`, Tracker) |
| `set_face` | Change the facial expression |
| `moment_photo` / `save_moment` | Photo of something interesting (bird, sunset), refused if a face is in view; saved only when Pip decides, to `data/photos/moments/` with a `.txt` note and a memory (see `senses.md`) |
| `remember` / `recall` | Long-term memory |
| `list_files` / `view_file` | See and open files: what the owner put in the shared folder `data/files`, its own photos and drawings (`files.md`) |
| `journal_search` / `people_notes` / `note_person` | Conversation journal and owner's notes about people (`journal.md`) |
| `run_self_test` | Run a hardware self-test |
| `draft_social_post` | Make a Bluesky post (see `social.md`) |
| `motion_mode` | Motion sensor mode off / wake / greet / security (security cannot be switched off by voice) |
| `draw_self_portrait` | Draw what it thinks it looks like in RoboOS Paint and post the video (runs in the background) |
| `draw` | Draw anything stroke by stroke in RoboOS Paint: what the owner asks for, itself, or what the camera sees (`from_photo`); saves the picture, records a video, posts it if asked (runs in the background) |
| `ask_owner` | Question with buttons on the screen, waits up to 60 s |
| `read_manual` | Read a page of this manual |
| `robodev` / `robodev_status` | Hand a change request to RoboDev, the coding agent (`robodev.md`), or ask what it is doing |
| `web_search` | Look something up on the internet (see "Looking things up online" below) |
| `web_page` | Read a public web page, shop page, text file or PDF, in parts or only the parts that mention some words |

## Looking things up online (since 2026-11-10)

`robotd/browse.py`, used by the brain and by RoboDev.

- **web_search**, Settings > AI > **Web search** (config `web.search`):
  - **Smart** (default): Perplexity Sonar through OpenRouter (`perplexity/sonar`). It returns a short answer with its
    sources and costs about half a US cent a search. At most `web.smart_per_day` (40) a day; after that it uses the
    free search. Each search is logged in `llm_calls` as model_key `web-search`.
  - **Free:** Bing's RSS feed, then Wikipedia. Results that share too few words with the query are dropped. It often
    finds nothing, because the free engines (DuckDuckGo, Bing, Mojeek, Yahoo) block or scramble robots.
  - **Off.**
- **web_page** (always free): downloads one page (up to 8 MB, 45 s, 6 redirects) and returns about 7000 characters at a
  time, or with `find`, the parts that mention those words, plus useful links (PDFs, datasheets). Shops' "Delivering to
  <town + ZIP>" lines are removed. PDFs are read with `pypdf` (installed in `venv` with the owner's OK on 2026-11-10,
  first 80 pages, at most 60 s). A 40-page datasheet takes about 40 s on the Pi the first time; the last 6 pages
  read stay cached for 15 minutes, so asking again about the same PDF is instant.
- **Safety:** only public internet addresses. The robot itself, the home network and other private addresses are refused,
  and so is every redirect step that leads to one. Every result starts with an "untrusted web content" label, and
  the brain's rules say never to follow instructions found on the web.
- Many datasheet sites (alldatasheet) answer 403 to robots. Amazon product pages work.

## Rules for the robot's AI

- Speak in 1 to 3 short plain sentences. No markdown, lists or emoji.
- You cannot move. Do not pretend to.
- Use `remember` when the owner tells you something worth keeping.
- Use `ask_owner` before anything risky (system changes, motors, deleting, spending money).
- If you are unsure how something works, call `read_manual`.

## When the on-board brain is down (no OpenRouter credit, no network)

Symptom: the robot says "Sorry, I could not reach my thinking brain just now" and the log shows `402 Payment Required`
(or another OpenRouter error). The hardware is fine. Social drafts get status `blocked` ("privacy check unavailable")
because the AI privacy check fails closed.

- **Owner's permission (2026-11-12):** a Claude Code session on the owner's PC may act as the robot's brain through the
  MCP tools or the same HTTP API ("it's the same Pip, just smarter", since the on-board brain is Claude Haiku anyway).
  That means answering for the robot (`robot_say`, `robot_face`), looking (`robot_look`), remembering, and running its
  tools. All the other rules still apply: ask before flashing, boot/system changes, motors, deleting data or spending money;
  no people, faces, addresses or nudity in anything public; never print or commit secrets.
- Over Wi-Fi the robot is at `pip.local` (or its IP address, see this robot's own notes); 192.168.77.2 only works on the Ethernet cable.
- A `blocked` draft is never retried by itself. If the owner approves a specific post while the AI check is down, check
  every frame yourself, set the draft back to `draft` with the reason in `privacy`, then publish it (`POST /api/social/publish`).
  Say in `privacy` that it was approved by hand, by whom, and why.
- Tell the robot what you did (`robot_remember`) so its own brain does not repeat it when it comes back.
