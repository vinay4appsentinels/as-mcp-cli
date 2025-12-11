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
as-mcp-cli <mcp_name> [--debug] <command>
```

### Arguments

- `mcp_name` - MCP server name as configured in Claude (e.g., `appsentinels`, `appsentinels-prod1`)
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

Credentials are automatically loaded from `~/.claude/.credentials.json`. This file is created by Claude Code when you authenticate with MCP servers.

The CLI looks for entries in the `mcpOAuth` section matching the provided `mcp_name` by the `serverName` field.

## How it Works

The CLI uses the MCP SSE (Server-Sent Events) protocol:

1. Connects to the SSE endpoint to establish a session
2. Sends an initialize request
3. Sends the command via POST to the message endpoint
4. Receives and displays the response from the SSE stream
