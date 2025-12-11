# as-mcp-cli

CLI for MCP (Model Context Protocol) servers using Claude credentials.

## Installation

```bash
pip install --user .
```

Or install directly:

```bash
pip install as-mcp-cli
```

## Usage

```bash
as-mcp-cli <command> [options]
```

### Commands

| Command | Description |
|---------|-------------|
| `mcp <name> <command>` | Run a command on an MCP server |
| `auth <name> [options]` | Authenticate with an MCP server |
| `add <name> <url>` | Add a new MCP server |
| `list` | List configured MCP servers |
| `remove <name>` | Remove an MCP server |

### Examples

```bash
# Run commands on MCP server
as-mcp-cli mcp appsentinels tenant all-tenants
as-mcp-cli mcp appsentinels api list nykaa_production --limit 10
as-mcp-cli mcp appsentinels --debug api tags list nykaa_production

# Add a new MCP server
as-mcp-cli add my-server https://example.com/mcp/sse

# List configured servers
as-mcp-cli list

# Re-authenticate
as-mcp-cli auth appsentinels --force

# Remove a server
as-mcp-cli remove my-server
```

## Commands

### `mcp` - Run Commands

Run commands on an MCP server:

```bash
as-mcp-cli mcp <name> [--debug] <command>
```

Options:
- `--debug` - Enable debug output

### `add` - Add Server

Add a new MCP server and authenticate:

```bash
as-mcp-cli add <name> <server-url> [--client-id ID]
```

### `auth` - Authenticate

Authenticate or re-authenticate with an MCP server:

```bash
as-mcp-cli auth <name> [options]
```

Options:
- `--server-url URL` - MCP server SSE URL (required for new servers)
- `--client-id ID` - OAuth client ID (optional)
- `--force` - Force re-authentication even if token is valid

### `list` - List Servers

List all configured MCP servers with token status:

```bash
as-mcp-cli list
```

### `remove` - Remove Server

Remove an MCP server from configuration:

```bash
as-mcp-cli remove <name>
```

## Authentication

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
