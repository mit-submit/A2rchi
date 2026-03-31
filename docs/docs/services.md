# Services

Archi supports several **services** — containerized applications that interact with the AI pipelines. Services are enabled at deploy time with the `--services` flag.

```bash
archi create [...] --services chatbot,uploader,grafana
```

List all available services with:

```bash
archi list-services
```

---

## Chat Interface

The primary user-facing service. Provides a web-based chat application for interacting with Archi's AI agents.

**Default port:** `7861`

### Key Features

- Streaming responses with tool-call visualization
- Agent selector dropdown for switching between agents
- Built-in [Data Viewer](data_sources.md#data-viewer) at `/data`
- Optional built-in MCP server at `/mcp/sse` for IDE and agent integrations
- Settings panel for model/provider selection
- [BYOK](models_providers.md#bring-your-own-key-byok) support
- Conversation history
- [Service Status Board & Alert Banners](#service-status-board--alert-banners)

### Configuration

```yaml
services:
  chat_app:
    agent_class: CMSCompOpsAgent
    agents_dir: examples/agents
    default_provider: local
    default_model: llama3.2
    trained_on: "Course documentation"
    hostname: "example.mit.edu"
    port: 7861
    external_port: 7861
```

### Running

```bash
archi create [...] --services chatbot
```

### Built-in MCP Server

The chat service can expose Archi itself as an MCP server over Server-Sent
Events. Enable it when you want tools like VS Code, Cursor, Claude Desktop, or
Claude Code to connect directly to your deployment.

```yaml
services:
  mcp_server:
    enabled: true
    url: "https://chat.example.org"
```

- **Endpoint:** `/mcp/sse`
- **Auth page:** `/mcp/auth` for generating bearer tokens when auth is enabled
- **Tools exposed:** query, document discovery, metadata search, content grep,
  chunk inspection, corpus stats, deployment info, and agent-spec inspection

---

## Service Status Board & Alert Banners

The Service Status Board (SSB) is a built-in feature of the Chat Interface that lets designated operators communicate service health, planned downtime, known issues, and general announcements directly to all users — without external tooling.

### How It Works

**Alert banners** appear as colour-coded strips at the top of every page in the chat app. Up to 5 active alerts are displayed at once. Each banner can be individually dismissed by the user client-side. A **details** link redirects to the full status board.

The **Status Board** at `/ssb/status` provides:

- **Active Alerts** — non-expired alerts with severity badges, creator, and timestamp
- **Expired Alerts** — historical record shown at reduced opacity
- **Post New Alert form** — visible only to configured alert managers

### Severity Levels

| Severity | Colour | Intended Use |
|----------|--------|--------------|
| `alarm` | Red | Service outage or critical failure |
| `warning` | Amber | Degraded performance, elevated error rate |
| `news` | Blue | Release notes, planned maintenance |
| `info` | Slate | General informational notices |

### Creating and Deleting Alerts

Navigate to **Status** in the main chat header (or go to `/ssb/status` directly). The **Post New Alert** form is shown to users who have alert manager access. Fill in:

- **Message** (required) — short text shown in the banner
- **Severity** (required) — one of `alarm`, `warning`, `news`, `info`
- **Description** (optional) — longer explanation shown only on the status page
- **Expires at** (optional) — datetime after which the alert is hidden from banners; expired alerts remain visible in the status board history

To delete an alert, click the **Delete** button on its card on the status board. Deletion is permanent.

Alerts can also be created via the REST API:

```bash
curl -X POST http://localhost:7861/api/ssb/alerts \
  -H 'Content-Type: application/json' \
  -d '{
    "severity": "warning",
    "message": "Embedding pipeline running — responses may be slower than usual",
    "description": "Optional longer explanation shown on the status board.",
    "expires_in_hours": 4
  }'
```

Or with an explicit expiry timestamp:

```bash
curl -X POST http://localhost:7861/api/ssb/alerts \
  -H 'Content-Type: application/json' \
  -d '{
    "severity": "alarm",
    "message": "Model backend unavailable",
    "expires_at": "2026-02-21T18:00:00"
  }'
```

### API Endpoints

| Method | Route | Auth Required | Description |
|--------|-------|---------------|-------------|
| `GET` | `/ssb/status` | Any authenticated user | Render the status board page |
| `POST` | `/api/ssb/alerts` | Alert managers only | Create a new alert |
| `DELETE` | `/api/ssb/alerts/<id>` | Alert managers only | Delete an alert by ID |

### Access Control

Alert managers are configured via `services.chat_app.alerts.managers` (username list) or the `alerts:manage` RBAC permission. The rules are:

1. **Auth disabled** → everyone may create and delete alerts.
2. **Auth enabled** → a user is an alert manager if **either**:
    - their username is in the `alerts.managers` list, **or**
    - their session roles grant the `alerts:manage` permission.
3. **Auth enabled, no username match, no `alerts:manage` permission** → nobody may manage (safe default; a warning is logged).

All users can always *view* alerts and the status board regardless of access level.

```yaml
# Username-based access (backwards compatible):
services:
  chat_app:
    alerts:
      managers:
        - alice
        - bob

# Role-based access (can be combined with the above):
services:
  chat_app:
    auth:
      auth_roles:
        roles:
          ops-team:
            permissions:
              - alerts:manage
```

See [Configuration → `services.chat_app.alerts`](configuration.md#serviceschat_appalerts) for the full reference.

---

## Document Upload

Document upload is exposed in the chat UI and backed by the **Data Manager** service. Documents can be uploaded via the web interface or by copying files directly into the data directory.

See [Data Sources — Adding Documents Manually](data_sources.md#adding-documents-manually) for setup instructions.

---

## Data Manager

A background service that handles data ingestion, vectorstore management, and scheduled re-scraping. It is automatically started with most deployments.

**Default port:** `7871`

### Features

- Orchestrates all data collectors (links, git, JIRA, Redmine)
- Manages the vectorstore (chunking, embedding, indexing)
- Provides a scheduling system for periodic re-ingestion
- Exposes API endpoints for ingestion status and schedule management

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/ingestion/status` | GET | Current ingestion progress |
| `/api/reload-schedules` | POST | Trigger schedule reload from database |
| `/api/schedules` | GET | Current schedule status |

### Configuration

```yaml
services:
  data_manager:
    port: 7871
    external_port: 7871
```

---

## Piazza Interface

Reads posts from a Piazza forum and posts draft responses to a specified Slack channel.

### Setup

1. Go to [Slack Apps](https://api.slack.com/apps) and sign in to your workspace.
2. Click **Create New App** → **From scratch**. Name the app and select the workspace.
3. Go to **Incoming Webhooks** under Features and toggle it on.
4. Click **Add New Webhook** and select the target channel.
5. Copy the **Webhook URL** to your secrets file.

### Configuration

Get the Piazza network ID from the class homepage URL (e.g., `https://piazza.com/class/m0g3v0ahsqm2lg` → `m0g3v0ahsqm2lg`).

```yaml
services:
  piazza:
    agent_class: QAPipeline
    provider: local
    model: llama3.2
    network_id: <your Piazza network ID>
  chat_app:
    trained_on: "Your class materials"
```

### Secrets

```bash
PIAZZA_EMAIL=...
PIAZZA_PASSWORD=...
SLACK_WEBHOOK=...
```

### Running

```bash
archi create [...] --services chatbot,piazza
```

---

## Redmine / Mailbox Interface

Reads new tickets in a Redmine project, drafts a response as a comment, and sends it as an email when the ticket is marked "Resolved" by an admin.

### Configuration

```yaml
services:
  redmine_mailbox:
    url: https://redmine.example.com
    project: my-project
    redmine_update_time: 10
    mailbox_update_time: 10
    answer_tag: "-- Archi -- Resolving email was sent"
```

### Secrets

```bash
IMAP_USER=...
IMAP_PW=...
REDMINE_USER=...
REDMINE_PW=...
SENDER_SERVER=...
SENDER_PORT=587
SENDER_REPLYTO=...
SENDER_USER=...
SENDER_PW=...
```

### Running

```bash
archi create [...] --services chatbot,redmine-mailer
```

---

## Mattermost Interface

Connects Archi to a Mattermost channel. Supports two operating modes:

- **Webhook mode** — Mattermost pushes outgoing webhooks to Archi (recommended)
- **Polling mode** — Archi polls a channel periodically via the Mattermost API

**Default port:** `5000`

### Setup

#### Secrets

```bash
# Required for webhook mode
MATTERMOST_WEBHOOK=https://mattermost.example.com/hooks/...  # Incoming webhook URL
MATTERMOST_OUTGOING_TOKEN=...                                 # Outgoing webhook token for request validation

# Required for polling mode only
MATTERMOST_PAK=...                       # Personal Access Token for the bot account
MATTERMOST_CHANNEL_ID_READ=...           # Channel to read posts from
MATTERMOST_CHANNEL_ID_WRITE=...          # Channel to post responses to

# Required for SSO auth (db mode)
SSO_CLIENT_ID=...
SSO_CLIENT_SECRET=...
BYOK_ENCRYPTION_KEY=...                  # Used to encrypt stored refresh tokens
PG_PASSWORD=...
```

#### Basic Configuration

```yaml
services:
  mattermost:
    update_time: 60       # polling interval in seconds (polling mode only)
    port: 5000
    external_port: 5000
```

#### Running

```bash
archi create [...] --services chatbot,mattermost
```

---

### Authentication

By default auth is disabled and the bot responds to all users. Two auth modes are available.

#### Mode 1: Config (Static Allowlist)

Roles are assigned to Mattermost users via a static map in the config. No SSO or database required.

```yaml
services:
  mattermost:
    auth:
      enabled: true
      token_store: config
      default_role: mattermost-restricted  # role for users not in user_roles
      user_roles:
        jsmith: [archi-expert]             # Mattermost username → list of roles
        ahmedmu: [archi-admins]
        someuser: [archi-expert, base-user]
```

- Users in `user_roles` get the specified roles.
- Users not in `user_roles` get `default_role`.
- If `default_role` is not defined in `auth_roles`, those users have no permissions and are denied.

#### Mode 2: DB / SSO (Recommended)

Roles come from the CERN SSO JWT token. On first message, the bot sends the user a login link. After authenticating, their roles are stored in the database and reused on subsequent messages — no re-login required until the session expires.

```yaml
services:
  mattermost:
    auth:
      enabled: true
      token_store: db
      session_lifetime_days: 30     # full re-login required after this period
      roles_refresh_hours: 24       # silent background role refresh interval
      login_base_url: "https://your-mattermost-service-host:5000"
      sso:
        server_metadata_url: "https://auth.cern.ch/auth/realms/cern/.well-known/openid-configuration"
        token_endpoint: "https://auth.cern.ch/auth/realms/cern/protocol/openid-connect/token"
```

**SSO registration requirement:** The callback URL `<login_base_url>/mattermost-auth/callback` must be registered as a valid redirect URI in your SSO client (Keycloak / CERN Auth).

**Login flow:**

```
1. User sends message to bot (no token stored)
2. Bot replies: "Please login: https://<host>:5000/mattermost-auth?state=<user_id>&username=<username>"
3. User clicks link → redirected to CERN SSO
4. After SSO login → redirected to /mattermost-auth/callback
5. Roles extracted from JWT, stored in mattermost_tokens table
6. User sees success page, closes tab, returns to Mattermost
7. Future messages use stored roles (silent refresh every 24h)
```

**Session lifecycle:**

| Event | Behaviour |
|-------|-----------|
| First message | Login link sent |
| Token valid, roles fresh | Respond normally |
| Roles stale (`> roles_refresh_hours`) | Silent refresh via stored refresh token |
| Session expired (`> session_lifetime_days`) | Login link sent again |
| Admin invalidates token | Login link sent on next message |

---

### Role-Based Access Control

Mattermost auth integrates with the same RBAC system used by the chat app. Roles are defined under `services.chat_app.auth.auth_roles`.

#### Restricting Access

To allow only users with a specific role (e.g. `archi-expert` and above), add the `mattermost:access` permission to those roles and **not** to `base-user`:

```yaml
services:
  chat_app:
    auth:
      auth_roles:
        roles:
          base-user:
            permissions:
              - chat:query
              - chat:history
              # no mattermost:access here

          archi-expert:
            inherits: [base-user]
            permissions:
              - mattermost:access   # grants access to the Mattermost bot
              - documents:view
              - config:view
              # ...

          archi-admins:
            permissions:
              - "*"                 # wildcard includes mattermost:access

        permissions:
          mattermost:access:
            description: "Access the Mattermost bot"
            category: "mattermost"
```

- `base-user` only → denied with "you don't have permission" message
- `archi-expert` → allowed (has `mattermost:access`)
- `archi-admins` → allowed (wildcard)

#### Tool-Level Permissions

Tool permissions work the same as in the chat app. Add permissions like `tools:http_get` to roles that should be able to use specific agent tools. The Mattermost user context is propagated through the full call stack so tool checks apply correctly.

```yaml
          archi-expert:
            permissions:
              - mattermost:access
              - tools:http_get      # allow HTTP GET tool for this role
```

#### Database

A `mattermost_tokens` table is required when using `token_store: db`. It is created automatically by `init.sql` on first deploy. For existing deployments, run the migration manually:

```sql
CREATE TABLE IF NOT EXISTS mattermost_tokens (
    mattermost_user_id  VARCHAR(255) PRIMARY KEY,
    mattermost_username VARCHAR(255),
    email               VARCHAR(255),
    roles               JSONB NOT NULL DEFAULT '[]',
    refresh_token       BYTEA,
    token_expires_at    TIMESTAMPTZ,
    roles_refreshed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Refresh tokens are encrypted at rest using `pgp_sym_encrypt` (requires `BYOK_ENCRYPTION_KEY`).

---

## Grafana Monitoring

Monitor system performance and LLM usage with a pre-configured Grafana dashboard.

**Default port:** `3000`

> **Note:** If redeploying with an existing name (without removing volumes), the PostgreSQL Grafana user may not have been created. Deploy a fresh instance to avoid issues.

### Configuration

```yaml
services:
  grafana:
    external_port: 3000
```

### Secrets

```bash
PG_PASSWORD=<your_database_password>
GRAFANA_PG_PASSWORD=<grafana_db_password>
```

### Running

```bash
archi create [...] --services chatbot,grafana
```

After deployment, access Grafana at `your-hostname:3000`. The default login is `admin`/`admin` — you'll be prompted to change the password on first login. Navigate to **Menu → Dashboards → Archi → Archi Usage** for the main dashboard.

> **Tip:** For the "Recent Conversation Messages" panel, click the three dots → **Edit** → find "Override 4" → enable **Cell value inspect** to expand long text entries. Click **Apply** to save.

---

## Grader Interface

An automated grading service for handwritten assignments with a web interface.

> **Note:** This service is experimental and not yet fully generalized.

### Requirements

The following files are needed:

- **`users.csv`**: Two columns — `MIT email` and `Unique code`
- **`solution_with_rubric_*.txt`**: One file per problem, named with the problem number. Begins with the problem name and a line of dashes.
- **`admin_password.txt`**: Admin code for resetting student attempts (passed as a secret).

### Configuration

```yaml
services:
  grader_app:
    provider: local
    model: llama3.2
    prompts:
      grading:
        final_grade_prompt: final_grade.prompt
      image_processing:
        image_processing_prompt: image_processing.prompt
    num_problems: 1
    local_rubric_dir: ~/grading/my_rubrics
    local_users_csv_dir: ~/grading/logins
  chat_app:
    trained_on: "rubrics, class info, etc."
```

### Secrets

```bash
ADMIN_PASSWORD=your_password
```

### Running

```bash
archi create [...] --services grader
```
