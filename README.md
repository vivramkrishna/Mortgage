# Lloyds Banking Demo Plugin

A production-styled demo of a ChatGPT-connected banking experience. It exposes
two banking scenarios — a **transaction viewer** and an interactive
**mortgage application flow** — to ChatGPT via both:

- **MCP (Model Context Protocol) over Streamable HTTP** — the modern way to
  connect a tool server to ChatGPT (Settings → Connectors).
- **Legacy OpenAPI Actions** — `ai-plugin.json` + `openapi.yaml`, for use with
  the classic GPT Builder / Actions interface.

Both interfaces call the same FastAPI backend and mock data, and both link into
a companion **Lloyds demo web application** for a deeper, full-dashboard
experience.

> **Where the inline UI comes from.** Asking for transactions renders a real
> interactive banking card *inside the ChatGPT conversation* — not Markdown
> text. That requires the **ChatGPT Apps SDK widget protocol**, which only the
> **MCP connector** path supports (see §4a). The legacy Actions path can only
> return text/Markdown. **If you want the UI component, connect via MCP.**

```
User: "Show my transactions"
  -> ChatGPT calls list_transactions (MCP) or GET /api/transactions (Actions)
  -> Not yet verified this session? -> email + OTP prompt (see below), then retried
  -> Interactive banking widget rendered inline in chat (MCP path)
  -> "Open in Lloyds Web App" button
  -> Click -> http://localhost:3000/transactions (full dashboard)

User: "@lloyds loan I want mortgage loan"
  -> ChatGPT calls start_mortgage_application -> asks one field at a time:
     loan amount, property value, then repayment term
  -> Once both are collected: "Please enter your email ID for authentication"
  -> ChatGPT calls submit_authentication_email(email=...)
     -> the BANK is asked for that customer first. Unknown address -> rejected,
        no code sent. Known address -> OTP sent.
  -> "Please enter the OTP for authentication"
  -> ChatGPT calls verify_authentication_otp(otp=...)
     -> wrong/expired code -> "Invalid OTP": the application is CANCELLED and
        the issued code is voided. Starting again issues a fresh code.
     -> correct code -> the bank underwrites the application against that
        customer's real profile and returns a priced offer -> success card
  -> Each mortgage application requires its own OTP before it can be submitted.
     The transactions flow reuses a successful verification for the rest of the
     session, so asking for transactions afterwards skips the prompt.
```

The email is not just an OTP destination — it **identifies the customer**. The
bank holds their income, credit score, date of birth, employment, deposit and
existing debts, so the chat only has to ask the three things the customer
actually chooses: how much, against what property, and over how long.

The property value is asked rather than read from the record because it is the
property they are buying *now*, which the bank has no way to know — and it is
what loan-to-value is measured against, so it visibly moves the decision:

| Property value (on a £250k / 25y request) | LTV | Rate | Offer |
|---|---|---|---|
| £500,000 | 50.00% | 4.20% | £250,000 |
| £400,000 | 62.50% | 4.20% | £250,000 |
| £280,000 | 89.29% | 4.35% | £250,000 |
| £260,000 | 96.15% | 4.55% | £247,000 — capped at 95% LTV |

```
                    ChatGPT
                       │  MCP (Streamable HTTP)
                       ▼
   backend :8000 ──HTTP──> bank :4000   ← the bank's own system
   (ChatGPT-facing)         │  customer records (3)
                            │  underwriting engine
                            └─ Command Centre dashboard
```

All data is static mock data — nothing here talks to a real bank.

### The three demo customers

The bank's whole customer book, in [bank/customers.py](bank/customers.py). Use one
of these addresses when the mortgage flow asks for an email — anything else is
rejected before a code is sent. They sit in different risk bands on purpose, so
the same request is priced differently:

Asking for £250,000 against a £400,000 property over 25 years:

| Email | Credit | Income | Rate | Offer |
|---|---|---|---|---|
| `konavivekramakrishna@gmail.com` | 781 | £95,000 | **4.20%** | £250,000 |
| `notimportantupdatesonly@gmail.com` | 712 | £62,000 | **5.05%** | £250,000 — self-employed loading |
| `sankranthibhuvaneswar@gmail.com` | 648 | £44,000 | **5.55%** | £198,000 — capped by income (4.5×) |

---

## 1. Project structure

```
backend/
  app.py                 FastAPI app: REST routes, MCP endpoint, plugin file routes
  mcp_server.py           MCP tools + widget wiring (low-level MCP Server API)
  widgets/
    transactions.html      The inline UI component rendered inside ChatGPT
                           (self-contained HTML/CSS/JS, text/html+skybridge)
  mock_data.py            Static transactions + legacy salary-only mortgage estimate
  models.py               Pydantic request/response schemas
  ui_cards.py             Renders the rich Markdown "cards" shown in chat
  store.py                In-memory bridge: chat-generated mortgage offers -> web app
  auth.py                 Email + OTP verification session
  email_client.py         SMTP delivery for verification codes
  bank_client.py          HTTP client for the bank core (:4000)
  config.py               Ports / URLs, overridable via env vars
  logging_conf.py         Logging setup

bank/                     The bank's own service (port 4000)
  app.py                  Bank API + serves the Command Centre
  customers.py            The 3 email-keyed customer records
  underwriting.py         Underwriting checks + offer pricing
  records.py              In-memory session records the dashboard reads
  public/
    command_centre.html    Internal dashboard: chat feed, checks, offer, goal plan

frontend/
  server.py               Static file server for the Lloyds demo web app (port 3000)
  public/
    index.html             Home
    transactions.html       Transactions dashboard
    mortgage.html            Mortgage application dashboard
    css/style.css, js/app.js

plugin/
  ai-plugin.template.json  Legacy plugin manifest template ({{PUBLIC_BASE_URL}} placeholder)
  openapi.template.yaml    OpenAPI 3.1 spec template
  ai-plugin.json           Generated (localhost by default; regenerated with the ngrok URL at startup)
  openapi.yaml             Generated
  logo.png                 Plugin logo

scripts/
  start.py                     Single-command launcher: bank + backend + frontend + ngrok
  generate_plugin_manifest.py  Renders plugin/*.template.* -> plugin/*.json|yaml
  test_mcp.py                  Protocol test: asserts the widget wiring is correct
```

### How the inline UI component works

Built against the official
[MCP Apps / ChatGPT UI docs](https://developers.openai.com/plugins/build/chatgpt-ui).
ChatGPT renders a tool result as a UI component only when the handshake is
satisfied. Three things have to line up:

1. **The tool references a UI resource.** `list_transactions` carries
   `_meta.ui.resourceUri = "ui://widget/transactions.html"` (the MCP Apps
   standard) *and* `_meta["openai/outputTemplate"]` (the ChatGPT alias for the
   same thing). This server emits both, since builds differ in which they honour.
2. **That URI resolves to an HTML resource** served with the MCP Apps UI MIME
   type **`text/html;profile=mcp-app`** (`backend/widgets/transactions.html`).
   The documented shape is `<div id="root"></div><script type="module">…</script>`,
   and it must be fully self-contained — the sandboxed iframe blocks external
   CDNs/fonts. CSP allowlists are declared in the resource's
   `_meta.ui.csp` (`connectDomains` / `resourceDomains` / `frameDomains`);
   all are empty here because the widget makes no network requests.
3. **The tool result carries both `structuredContent` and the same `_meta`.**
   The component receives the data via the `ui/notifications/tool-result`
   postMessage notification (with `window.openai.toolOutput` as the ChatGPT
   compat path — the widget supports both). The `_meta` on the *result* is what
   tells ChatGPT to render the template at all.

If your ChatGPT build is older and expects the previous `text/html+skybridge`
MIME type, flip it without touching code:

```bash
WIDGET_MIME_MODE=skybridge python scripts/start.py
```

`backend/mcp_server.py` uses the **low-level MCP Server API** rather than
FastMCP precisely so it can control `_meta` and `structuredContent` exactly.

Three gotchas that are easy to get wrong and produce a silently text-only
result — all three were hit while building this:

- **`_meta` must be on the tool _result_, not just the tool definition.**
  This is the big one. Advertising `openai/outputTemplate` in `tools/list`
  is *not* sufficient: every `tools/call` result must repeat it, otherwise
  the client gets `structuredContent` with no idea which template to render
  and falls back to printing text. The SDK's `@server.call_tool()` decorator
  hardcodes `CallToolResult(...)` with no `_meta` field, which is why this
  server registers `request_handlers[CallToolRequest]` manually.
- `_meta` must be passed using its **alias**, i.e.
  `types.Tool(..., **{"_meta": {...}})`. These models don't set
  `populate_by_name`, so a `meta=` kwarg is silently swallowed as an extra
  field and never serialised as `_meta`.
- The model-facing `content` is kept to a one-line summary. If you also return
  the full transaction list as text, the model tends to re-print it as a
  Markdown table *underneath* the widget, duplicating the UI.

**Only `list_transactions` renders a widget**, and only once the user has
verified their email/OTP this session — before that (and for the mortgage
flow throughout) tools intentionally return Markdown, per the demo's design.

---

## 2. Setup

Requires Python 3.10+.

```bash
cd chatgpt_plugin
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure SMTP (for OTP verification codes)

The mortgage submission and transactions flows are gated behind an email +
one-time-passcode (OTP) check, sent via real SMTP (not mocked). Copy
`.env.example` to `.env` and fill in:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=<an app password, not your account password>
SMTP_FROM_EMAIL=you@gmail.com
```

Any standard SMTP provider works — Gmail (needs a Google
[App Password](https://myaccount.google.com/apppasswords)), Outlook, SendGrid,
or a free testing sandbox like [Mailtrap](https://mailtrap.io). See the
comments in `.env.example` for provider-specific hosts/ports.

Gmail app passwords are shown as four groups (`abcd efgh ijkl mnop`) — strip
the spaces, `SMTP_PASSWORD` must be one token. `SMTP_FROM_EMAIL` falls back to
`SMTP_USERNAME` if left blank.

`.env` is read automatically at startup (by `backend/config.py`, `bank/app.py`
and `scripts/start.py`), so there is nothing to source by hand. Real
environment variables still take precedence over the file, so
`BANK_PORT=4100 python scripts/start.py` overrides whatever `.env` says.

For local testing without checking a real inbox, also set
`DEBUG_EXPOSE_OTP=true` — this exposes the generated code at
`GET /api/_debug/otp` and a state reset at `POST /api/_debug/reset`, both used
by `scripts/test_mcp.py` (the reset is what makes the suite re-runnable, since
a successful verification otherwise lasts an hour). **Never enable this in a
real deployment.**

### Install ngrok (if not already installed)

```bash
# macOS
brew install ngrok

# Linux (snap)
sudo snap install ngrok

# or download directly: https://ngrok.com/download
```

Sign up at https://dashboard.ngrok.com/signup (free tier is enough), then grab
your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken and
run once:

```bash
ngrok config add-authtoken <YOUR_AUTHTOKEN>
```

---

## 3. Run it (single command)

```bash
source .venv/bin/activate
python scripts/start.py
```

This starts, in one process tree:

1. The bank core on `http://localhost:4000` (customer records, underwriting,
   Command Centre) — started first, since the backend depends on it
2. The FastAPI backend on `http://localhost:8000` (REST API + `/mcp` MCP endpoint)
3. The Lloyds demo web app on `http://localhost:3000`
4. An ngrok tunnel exposing the backend publicly
5. Regenerates `plugin/ai-plugin.json` and `plugin/openapi.yaml` with the live
   ngrok URL automatically

You'll see output like:

```
========================================================================
                       Lloyds Banking Demo Plugin
========================================================================
  Backend (local):     http://localhost:8000
  Backend (public):    https://abcd-1234.ngrok-free.app
  MCP endpoint:        https://abcd-1234.ngrok-free.app/mcp
  OpenAPI spec:        https://abcd-1234.ngrok-free.app/openapi.yaml
  Plugin manifest:     https://abcd-1234.ngrok-free.app/.well-known/ai-plugin.json
  Lloyds web app:      http://localhost:3000
  Bank Command Centre: http://localhost:4000
  API docs (Swagger):  http://localhost:8000/docs
========================================================================
```

The **Bank Command Centre** at `http://localhost:4000` is the bank-side view.
Leave it open while you run a mortgage in ChatGPT: it polls every 2 seconds and
shows the live chat feed, all seven underwriting checks with their risk points,
the decision, the priced offer, and the goal plan ticking through
`GATHER_DATA → RUN_CHECKS → CALCULATE_OFFER → PRESENT_OFFER`.

To run without ngrok (local-only testing, e.g. against a locally-hosted GPT
Builder or just to poke the API):

```bash
python scripts/start.py --no-ngrok
```

Custom ports:

```bash
python scripts/start.py --backend-port 8010 --frontend-port 3010
```

Stop everything with `Ctrl+C` — it cleanly shuts down the backend, frontend,
and ngrok tunnel.

---

## 4. Connect to ChatGPT

### Option A — MCP Connector (required for the inline UI widget)

1. In ChatGPT, go to **Settings → Connectors → Create** (or **Advanced → Developer mode**,
   depending on your ChatGPT plan).
2. Paste the MCP endpoint printed by `start.py`, e.g.
   `https://abcd-1234.ngrok-free.app/mcp` (`/mcp` and `/mcp/` both work — no
   redirect either way).
3. Authentication: **None** (this demo has no auth).
4. Save, then enable the connector in a chat.

Ask *"show my transactions"* and you should get the rendered banking card, not
a Markdown table. If you see plain text instead, see Troubleshooting below.

### Option B — Legacy Actions / Plugin (text only, no widget)

1. In a GPT Builder / Custom GPT, go to **Configure → Actions → Create new action**.
2. Choose **Import from URL** and paste the OpenAPI spec URL, e.g.
   `https://abcd-1234.ngrok-free.app/openapi.yaml`.
3. Authentication: **None**.
4. Save.

### Example prompts to try

- "Show my transactions"
- "List all my transactions"
- "View my transaction history"
- "I want a mortgage"
- "Apply for a mortgage loan" → then reply with a salary, e.g. "£50,000"

---

## 5. Testing without ChatGPT

### Health check

```bash
curl http://localhost:8000/health
```

### Transactions

```bash
curl http://localhost:8000/api/transactions | python3 -m json.tool
```

### Mortgage flow (legacy REST/Actions — salary only, no auth)

```bash
curl -X POST http://localhost:8000/api/mortgage/start | python3 -m json.tool

curl -X POST http://localhost:8000/api/mortgage/estimate \
  -H "Content-Type: application/json" \
  -d '{"annual_salary": 50000}' | python3 -m json.tool
```

Expected offer for a £50,000 salary: £250,000 loan, 4.2% rate, 25-year term,
~£1,347/month.

The full multi-field + email/OTP mortgage journey described at the top of
this README only exists over MCP (`start_mortgage_application`,
`provide_mortgage_details`, `submit_authentication_email`,
`verify_authentication_otp`) — see the next section to exercise it without
ChatGPT.

### MCP + widget protocol (recommended check)

With the stack running, connect as a real MCP client and assert the whole
Apps SDK handshake:

```bash
python scripts/test_mcp.py                       # defaults to localhost:8000
python scripts/test_mcp.py http://localhost:8050 # custom port
```

It verifies the `outputTemplate` metadata, that the widget resource is served
as `text/html+skybridge`, that `structuredContent` is returned with all the
fields the widget reads, and that the mortgage tools stay widget-free.
Exits non-zero if anything regresses.

### Lloyds web app

Open `http://localhost:3000` in a browser, or navigate directly to
`/transactions` and `/mortgage`. If the frontend can't reach the backend at
its default `http://localhost:8000`, pass `?api=<backend-url>` in the URL
(the backend's CORS is open for this demo).

### Interactive API docs

FastAPI serves Swagger UI at `http://localhost:8000/docs` and ReDoc at
`http://localhost:8000/redoc`.

---

## 6. Troubleshooting

**"Port 8000 is already in use"** — something else on your machine owns that
port. `scripts/start.py` now checks this up front and refuses to start rather
than half-starting. Either free the port (`lsof -i :8000`) or run on another:

```bash
python scripts/start.py --backend-port 8010 --frontend-port 3010
```

**No UI component in chat, just text** — work through these in order:

1. You must be connected via the **MCP connector**, not legacy Actions.
   Actions cannot render widgets, only text.
2. Run `python scripts/test_mcp.py <base-url>`. The check that matters most is
   *"RESULT carries outputTemplate"* — if `tools/call` returns
   `structuredContent` but no `_meta`, ChatGPT will render a Markdown table
   instead of the component. (A telltale sign: the reply shows a *complete*
   transaction table even though the tool's text output is one line — the
   model is reading `structuredContent` and formatting it itself.)
3. **Restart the backend**, then remove and re-add the connector in ChatGPT.
   Tool metadata is cached when the connector is added, so server changes made
   *after* connecting are not picked up until you refresh it. If you're using
   ngrok, the URL changes on restart — update the connector accordingly.
4. Start a **new chat**. An existing thread can keep reusing the tool
   definitions it already cached.
5. Try the other MIME convention — this is the most likely culprit if
   everything else checks out, since the standard moved from
   `text/html+skybridge` to `text/html;profile=mcp-app`:

   ```bash
   WIDGET_MIME_MODE=skybridge python scripts/start.py
   ```

6. Confirm your ChatGPT plan/workspace has UI component rendering enabled —
   it isn't available on every tier, and free accounts in particular may only
   get the text fallback.

**The banner shows `http://localhost:8000` instead of an ngrok URL** — the
tunnel failed and the launcher fell back to localhost-only, which ChatGPT
cannot reach. Look further up the log for the reason.

The usual cause is `ERR_NGROK_334` / *"endpoint is already online"*: the free
tier permits a single online endpoint on your reserved domain, and an agent
left over from an earlier run is still holding it. `scripts/start.py` handles
this two ways now:

- If the leftover tunnel already points at the backend port, it is **reused**
  and you'll see `Reusing the ngrok tunnel already serving port 8000`.
- Otherwise the error names the remedy directly:

  ```bash
  pkill -f 'ngrok start'   # then run scripts/start.py again
  ```

Check what's currently tunnelling with `curl -s localhost:4040/api/tunnels`.
The launcher also stops any agent it started on `Ctrl+C`, *including* when the
tunnel itself failed — that omission was what left orphans holding the domain.

Or just run with `--no-ngrok` if you only need local testing.

## 7. Notes & limitations (demo scope)

- All banking data is synthetic — `backend/mock_data.py` for transactions,
  `bank/customers.py` for the three customer records. There is no real ledger,
  account, or credit bureau behind this.
- Underwriting (`bank/underwriting.py`) is a faithful port of the ACP mortgage
  sub-agent's skill classes, rewritten as plain functions. Two defects in the
  original were fixed on the way in, both marked `BUGFIX` in the source:
  1. The offer step read `max_loan_by_ltv`, a key the LTV check never returned,
     so the lookup always missed and the **LTV cap was never applied**.
  2. LTV is returned as a percentage (`50.0`) but was compared against ratios
     (`> 0.90`), which is true for virtually every applicant — so **everyone
     silently picked up the +0.35% high-LTV loading**.
- `calculate_mortgage_offer` in `backend/mock_data.py` still exists but now only
  backs the legacy `/api/mortgage/estimate` Actions endpoint. The MCP journey
  goes through the bank.
- `backend/store.py` is an in-memory bridge so the Lloyds web app can show the
  mortgage offer that was just generated in chat; it resets when the backend
  restarts and isn't multi-user safe — fine for a single-user local demo.
- Email + OTP verification (`backend/auth.py`) is a single, process-global,
  in-memory session — again fine for one user in one demo process, but not a
  real multi-tenant auth system. A wrong code cancels the pending request
  outright (no retries) and codes expire after 5 minutes.
- `provide_mortgage_details` deliberately accepts aliased and unrecognised
  parameter names, applying any numeric argument to the question currently
  being asked. Models paraphrase parameter names and clients cache old tool
  schemas; without this the flow silently dropped answers and re-asked the
  same question forever.
- REST endpoints other than `/api/_debug/otp` are not authenticated; the
  debug OTP endpoint itself is off by default (`DEBUG_EXPOSE_OTP=false`) and
  must never be enabled outside local testing.
