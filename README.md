# as-mcp-cli

Pass-through CLI for MCP (Model Context Protocol) servers using Claude credentials.

## Installation

```bash
pip install -e .
```

Or install directly:

```bash
pip install as-mcp-cli
```

## Usage

```bash
# Run commands
as-mcp-cli <mcp_name> [--debug] <command>

# Authenticate
as-mcp-cli auth <mcp_name> [options]
```

### Arguments

- `mcp_name` - MCP server name (e.g., `appsentinels`, `appsentinels-prod1`)
- `command` - Command to pass through to the MCP server

### Options

- `--debug` - Enable debug output
- `-h, --help` - Show help message

### Examples

```bash
# List all tenants
as-mcp-cli appsentinels tenant all-tenants

# List APIs with limit
as-mcp-cli appsentinels-prod1 api list nykaa_production --limit 10

# With debug output
as-mcp-cli appsentinels --debug api tags list nykaa_production
```

## Authentication

### Using Existing Credentials

Credentials are automatically loaded from `~/.claude/.credentials.json`. This file is created by Claude Code when you authenticate with MCP servers.

### Manual Authentication

You can authenticate or re-authenticate using the `auth` command:

```bash
# Authenticate with a new MCP server
as-mcp-cli auth my-server --server-url https://example.com/mcp/sse

# Re-authenticate (force new login)
as-mcp-cli auth appsentinels --force

# Authenticate with specific client ID
as-mcp-cli auth appsentinels --server-url https://example.com/mcp/sse --client-id my-client-id
```

### Auth Options

- `--server-url URL` - MCP server SSE URL (required for new servers)
- `--client-id ID` - OAuth client ID (optional, will use existing or attempt dynamic registration)
- `--force` - Force re-authentication even if a valid token exists

### OAuth Flow

The CLI uses OAuth 2.0 with PKCE for authentication:

1. Discovers OAuth endpoints via `.well-known/oauth-authorization-server`
2. Opens browser for user authentication
3. Receives callback with authorization code
4. Exchanges code for access and refresh tokens
5. Stores credentials in `~/.claude/.credentials.json`

Tokens are automatically refreshed when expired (if refresh token is available).

## How it Works

The CLI uses the MCP SSE (Server-Sent Events) protocol:

1. Connects to the SSE endpoint to establish a session
2. Sends an initialize request
3. Sends the command via POST to the message endpoint
4. Receives and displays the response from the SSE stream
