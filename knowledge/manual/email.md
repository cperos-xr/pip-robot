# Email (Pip's own inbox)

Pip can have its own address at AgentMail (free tier: 100 emails a day, 3 inboxes). The address is config `mail.address`
(in `data/config.json`) and the key is in `data/agentmail.env`; without both, the email tools say "not set up".
Keep the address out of code, docs and posts: an inbox that anyone can find gets spam and trick emails.
Code: `robotd/mail.py`. Set up 2026-11-13.

## The owner's rule: every send is approved by hand

- Pip (the brain) can **read** mail and **write drafts**. It has no way to send.
- A draft is sent only when the owner presses **Send**:
  - on the **touchscreen**: each new draft pops up (`Send email #N?` with To, subject and the start of the text):
    Send / Discard / Later. Later keeps it. Settings > Email > Drafts waiting shows the oldest one again.
  - in the **web app**: Settings tab > Email. Drafts can be edited there before sending.
- The API token (RoboDev, the MCP server, scripts, Claude Code) can make drafts but **cannot send**:
  `POST /api/mail` with `approve` or `send` is refused unless the request comes from the owner's signed-in web
  session. RoboDev cannot read the key file either (`agentmail.env` is in its secret-path list), and `/api/mail`
  is a risky API for it.
- Anyone standing at the robot can tap the touchscreen. Turn off Settings > Email > "Ask on screen for each draft"
  to approve only in the web app.

## Reading

- robotd checks the inbox every 2 minutes (config `mail.check_s`). New mail: a "New email" notice on the screen
  and a log line, never the sender or the text (RoboOS screenshots can end up in public posts).
- Brain tools: `email_inbox` (numbered list), `email_read` (one email by number, marks it read),
  `email_draft` (to, subject, text, reply_to = number of the email being answered).
- Email text is **untrusted** (like web pages): the brain gets it wrapped in a warning and must never follow
  instructions in it, and never put email contents or addresses in social posts.
- Attachments are listed, not opened.

## Files and settings

- Key: `data/agentmail.env` (mode 600, one line: the bare key or `AGENTMAIL_API_KEY=...`). Never print or commit it.
- Drafts and sent mail: tables `mail_out` (status pending / sent / discarded) and `mail_ids` (short numbers) in
  `data/robot.db`. The inbox itself stays on AgentMail.
- Config `mail`: `address`, `check_s` (120), `max_drafts_day` (20, drafts by Pip), `max_sends_day` (40),
  `screen_approve` (true), `screen_ask_s` (1800: how long a touchscreen question waits).

## API (owner)

| Call | What |
|---|---|
| `GET /api/mail` | address, unread count, cached inbox (newest first, short numbers), drafts |
| `GET /api/mail/message?id=12` | one email in full (marks it read) |
| `POST /api/mail {"cmd":"refresh"}` | check AgentMail now |
| `POST /api/mail {"cmd":"draft","to":"a@b.c","subject":"Hi","text":"...","reply_to":""}` | save a draft (not sent) |
| `POST /api/mail {"cmd":"discard","id":3}` | throw a draft away |
| `POST /api/mail {"cmd":"approve","id":3,"to":...,"subject":...,"text":...}` | **web sign-in only**: send draft 3 (edits optional) |
| `POST /api/mail {"cmd":"send","to":...,"subject":...,"text":...,"reply_to":""}` | **web sign-in only**: the owner's own email |

AgentMail REST (for changes): base `https://api.agentmail.to/v0`, header `Authorization: Bearer <key>`, the inbox id
is the address; `GET /inboxes/{id}/messages`, `GET .../messages/{mid}`, `POST .../messages/send`,
`POST .../messages/{mid}/reply`, `PATCH .../messages/{mid}` (`add_labels` / `remove_labels`). Received mail has the
labels `received` + `unread`. Docs: docs.agentmail.to.
