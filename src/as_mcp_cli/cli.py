#!/usr/bin/env python3
"""
AppSentinels MCP CLI - Pass-through CLI for MCP servers.
Uses token from ~/.claude/.credentials.json

MCP uses SSE (Server-Sent Events) protocol:
1. Connect to SSE endpoint to get session
2. Send initialize request
3. Send command via POST to message endpoint
4. Receive response on SSE stream
"""

import json
import sys
import requests
import uuid
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

DEBUG = False


def debug_print(msg):
    if DEBUG:
        print(f"Debug: {msg}", file=sys.stderr)


def load_credentials(mcp_name):
    """Load credentials from ~/.claude/.credentials.json

    Args:
        mcp_name: MCP server name to use (e.g., 'appsentinels-prod1', 'appsentinels')

    Returns tuple of (access_token, sse_url, base_url)
    """
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        print(f"Error: Credentials file not found at {creds_path}", file=sys.stderr)
        sys.exit(1)

    with open(creds_path) as f:
        creds = json.load(f)

    mcp_oauth = creds.get("mcpOAuth", {})

    # Find the specified server
    for key, value in mcp_oauth.items():
        server_name = value.get("serverName", "")
        if server_name == mcp_name:
            if value.get("accessToken") and value.get("serverUrl"):
                return _extract_urls(value)

    print(f"Error: MCP server '{mcp_name}' not found in credentials", file=sys.stderr)
    print(f"Available servers:", file=sys.stderr)
    seen = set()
    for key, value in mcp_oauth.items():
        name = value.get("serverName")
        if name and name not in seen:
            print(f"  - {name}", file=sys.stderr)
            seen.add(name)
    sys.exit(1)


def _extract_urls(cred_entry):
    """Extract token and URLs from credential entry"""
    token = cred_entry["accessToken"]
    sse_url = cred_entry["serverUrl"]
    # Derive base URL by removing the /mcp/sse path
    if sse_url.endswith("/mcp/sse"):
        base_url = sse_url[:-8]  # Remove "/mcp/sse"
    else:
        # Fall back to extracting scheme://host
        parsed = urlparse(sse_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
    return token, sse_url, base_url


class MCPSession:
    def __init__(self, token, sse_url, base_url):
        self.token = token
        self.sse_url = sse_url
        self.base_url = base_url
        self.message_url = None
        self.initialized = False
        self.response = None
        self.results = {}

    def connect_and_run(self, command):
        """Connect to SSE and run command"""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }

        request_id = str(uuid.uuid4())
        init_id = str(uuid.uuid4())

        try:
            self.response = requests.get(self.sse_url, headers=headers, stream=True, timeout=(10, 120))
            self.response.raise_for_status()

            current_event = None
            data_buffer = []

            for line in self.response.iter_lines(decode_unicode=True):
                if line is None:
                    continue

                line_str = line.strip() if isinstance(line, str) else ""

                if not line_str:
                    if current_event and data_buffer:
                        data_str = "".join(data_buffer)
                        self._handle_event(current_event, data_str, command, request_id, init_id)

                        # Check if we got our result
                        if request_id in self.results:
                            self.response.close()
                            return self.results[request_id]

                    current_event = None
                    data_buffer = []
                    continue

                if line_str.startswith("event:"):
                    current_event = line_str[6:].strip()
                elif line_str.startswith("data:"):
                    data_buffer.append(line_str[5:].strip())

            return None

        except requests.exceptions.Timeout:
            print("Error: Connection timed out", file=sys.stderr)
            return None
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}", file=sys.stderr)
            return None

    def _handle_event(self, event, data, command, request_id, init_id):
        debug_print(f"Event: {event}, Data: {data[:200]}...")

        if event == "endpoint":
            # Got message endpoint
            if data.startswith("/"):
                self.message_url = self.base_url + data
            else:
                try:
                    d = json.loads(data)
                    url = d.get("url", data)
                    self.message_url = self.base_url + url if url.startswith("/") else url
                except:
                    self.message_url = self.base_url + data

            debug_print(f"Message URL: {self.message_url}")

            # Send initialize first, then command
            def send_messages():
                msg_headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                }

                # Initialize
                init_payload = {
                    "jsonrpc": "2.0",
                    "id": init_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "as-mcp-cli",
                            "version": "1.0.0"
                        }
                    }
                }
                debug_print(f"Sending initialize")
                requests.post(self.message_url, headers=msg_headers, json=init_payload, timeout=30)

                # Small delay
                time.sleep(0.5)

                # Send initialized notification
                notif_payload = {
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized"
                }
                requests.post(self.message_url, headers=msg_headers, json=notif_payload, timeout=30)

                time.sleep(0.5)

                # Now send the actual command
                cmd_payload = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "cli_execute",
                        "arguments": {
                            "command": command
                        }
                    }
                }
                debug_print(f"Sending command: {command}")
                requests.post(self.message_url, headers=msg_headers, json=cmd_payload, timeout=120)

            thread = threading.Thread(target=send_messages)
            thread.start()

        elif event == "message":
            try:
                msg = json.loads(data)
                msg_id = msg.get("id")
                if msg_id:
                    self.results[msg_id] = msg
                    debug_print(f"Got result for {msg_id}")
            except json.JSONDecodeError:
                pass


def run_command(command, mcp_name):
    """Run a command through the MCP"""
    token, sse_url, base_url = load_credentials(mcp_name)
    debug_print(f"Using MCP: {mcp_name}")
    debug_print(f"Using SSE URL: {sse_url}")
    debug_print(f"Using Base URL: {base_url}")
    session = MCPSession(token, sse_url, base_url)
    result = session.connect_and_run(command)

    if result:
        if "result" in result:
            content = result["result"].get("content", [])
            for item in content:
                if item.get("type") == "text":
                    text = item.get("text", "")
                    try:
                        parsed = json.loads(text)
                        print(json.dumps(parsed, indent=2))
                    except json.JSONDecodeError:
                        print(text)
                    return 0
        elif "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            return 1

        print(json.dumps(result, indent=2))
        return 0

    print("Error: No response received", file=sys.stderr)
    return 1


def print_help():
    """Print help message"""
    print("as-mcp-cli - CLI for MCP servers")
    print("")
    print("Usage:")
    print("  as-mcp-cli <command> [options]")
    print("")
    print("Commands:")
    print("  mcp <name> <command>    Run a command on an MCP server")
    print("  auth <name> [options]   Authenticate with an MCP server")
    print("  add <name> <url>        Add a new MCP server")
    print("  list                    List configured MCP servers")
    print("  remove <name>           Remove an MCP server")
    print("")
    print("Options:")
    print("  --debug     Enable debug output")
    print("  -h, --help  Show this help message")
    print("")
    print("Examples:")
    print("  as-mcp-cli mcp appsentinels tenant all-tenants")
    print("  as-mcp-cli mcp appsentinels --debug api list tenant_name")
    print("  as-mcp-cli auth appsentinels --force")
    print("  as-mcp-cli add my-server https://example.com/mcp/sse")
    print("  as-mcp-cli list")
    print("")
    print("Credentials are stored in ~/.claude/.credentials.json")


def run_mcp_command(args):
    """Handle mcp subcommand - run commands on MCP server"""
    global DEBUG

    if not args or args[0] in ("-h", "--help"):
        print("Usage: as-mcp-cli mcp <name> [--debug] <command>")
        print("")
        print("Run a command on an MCP server.")
        print("")
        print("Arguments:")
        print("  name       MCP server name")
        print("  command    Command to pass through to the MCP server")
        print("")
        print("Options:")
        print("  --debug    Enable debug output")
        print("")
        print("Examples:")
        print("  as-mcp-cli mcp appsentinels tenant all-tenants")
        print("  as-mcp-cli mcp appsentinels api list nykaa_production --limit 10")
        print("  as-mcp-cli mcp appsentinels --debug api tags list nykaa_production")
        return 0

    mcp_name = args[0]
    args = args[1:]

    if not args:
        print(f"Error: No command provided for MCP '{mcp_name}'", file=sys.stderr)
        return 1

    if args[0] == "--debug":
        DEBUG = True
        args = args[1:]

    if not args:
        print(f"Error: No command provided after --debug", file=sys.stderr)
        return 1

    command = " ".join(args)
    return run_command(command, mcp_name)


def run_auth_command(args):
    """Handle auth subcommand"""
    from .auth import run_auth

    if not args or args[0] in ("-h", "--help"):
        print("Usage: as-mcp-cli auth <name> [options]")
        print("")
        print("Authenticate with an MCP server using OAuth.")
        print("")
        print("Arguments:")
        print("  name               MCP server name to authenticate with")
        print("")
        print("Options:")
        print("  --server-url URL   MCP server SSE URL (required for new servers)")
        print("  --client-id ID     OAuth client ID (optional)")
        print("  --force            Force re-authentication even if token is valid")
        print("  -h, --help         Show this help message")
        print("")
        print("Examples:")
        print("  as-mcp-cli auth my-server --server-url https://example.com/mcp/sse")
        print("  as-mcp-cli auth appsentinels --force")
        print("  as-mcp-cli auth appsentinels --client-id my-client-id")
        return 0

    mcp_name = args[0]
    args = args[1:]

    server_url = None
    client_id = None
    force = False

    # Parse options
    i = 0
    while i < len(args):
        if args[i] == "--server-url" and i + 1 < len(args):
            server_url = args[i + 1]
            i += 2
        elif args[i] == "--client-id" and i + 1 < len(args):
            client_id = args[i + 1]
            i += 2
        elif args[i] == "--force":
            force = True
            i += 1
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            return 1

    return run_auth(mcp_name, server_url, client_id, force)


def run_add_command(args):
    """Handle add subcommand - add a new MCP server"""
    from .auth import run_auth

    if not args or args[0] in ("-h", "--help") or len(args) < 2:
        print("Usage: as-mcp-cli add <name> <server-url> [--client-id ID]")
        print("")
        print("Add a new MCP server and authenticate.")
        print("")
        print("Arguments:")
        print("  name         Name for the MCP server")
        print("  server-url   MCP server SSE URL")
        print("")
        print("Options:")
        print("  --client-id ID   OAuth client ID (optional)")
        print("")
        print("Examples:")
        print("  as-mcp-cli add my-server https://example.com/mcp/sse")
        print("  as-mcp-cli add my-server https://example.com/mcp/sse --client-id abc123")
        return 0 if args and args[0] in ("-h", "--help") else 1

    mcp_name = args[0]
    server_url = args[1]
    args = args[2:]

    client_id = None

    # Parse options
    i = 0
    while i < len(args):
        if args[i] == "--client-id" and i + 1 < len(args):
            client_id = args[i + 1]
            i += 2
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            return 1

    return run_auth(mcp_name, server_url, client_id, force=True)


def run_list_command(args):
    """Handle list subcommand - list configured MCP servers"""
    if args and args[0] in ("-h", "--help"):
        print("Usage: as-mcp-cli list")
        print("")
        print("List all configured MCP servers.")
        return 0

    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        print("No credentials file found.")
        return 0

    with open(creds_path) as f:
        creds = json.load(f)

    mcp_oauth = creds.get("mcpOAuth", {})

    if not mcp_oauth:
        print("No MCP servers configured.")
        return 0

    print("Configured MCP servers:")
    print("")

    seen = {}
    for key, value in mcp_oauth.items():
        name = value.get("serverName", "unknown")
        url = value.get("serverUrl", "unknown")
        expires_at = value.get("expiresAt", 0)

        # Check token status
        import time
        now = time.time() * 1000
        if expires_at == 0:
            status = "no expiry"
        elif expires_at > now:
            hours_left = (expires_at - now) / (1000 * 60 * 60)
            if hours_left > 24:
                status = f"valid ({int(hours_left/24)}d left)"
            else:
                status = f"valid ({int(hours_left)}h left)"
        else:
            status = "expired"

        if name not in seen:
            seen[name] = True
            print(f"  {name}")
            print(f"    URL: {url}")
            print(f"    Token: {status}")
            print("")

    return 0


def run_remove_command(args):
    """Handle remove subcommand - remove an MCP server"""
    if not args or args[0] in ("-h", "--help"):
        print("Usage: as-mcp-cli remove <name>")
        print("")
        print("Remove an MCP server from configuration.")
        print("")
        print("Arguments:")
        print("  name    MCP server name to remove")
        return 0 if args and args[0] in ("-h", "--help") else 1

    mcp_name = args[0]

    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        print("No credentials file found.", file=sys.stderr)
        return 1

    with open(creds_path) as f:
        creds = json.load(f)

    mcp_oauth = creds.get("mcpOAuth", {})

    # Find and remove entries
    to_remove = []
    for key, value in mcp_oauth.items():
        if value.get("serverName") == mcp_name:
            to_remove.append(key)

    if not to_remove:
        print(f"MCP server '{mcp_name}' not found.", file=sys.stderr)
        return 1

    for key in to_remove:
        del mcp_oauth[key]

    with open(creds_path, "w") as f:
        json.dump(creds, f, indent=2)

    print(f"Removed MCP server '{mcp_name}'.")
    return 0


def main():
    global DEBUG

    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print_help()
        sys.exit(0)

    cmd = args[0]
    cmd_args = args[1:]

    if cmd == "mcp":
        sys.exit(run_mcp_command(cmd_args))
    elif cmd == "auth":
        sys.exit(run_auth_command(cmd_args))
    elif cmd == "add":
        sys.exit(run_add_command(cmd_args))
    elif cmd == "list":
        sys.exit(run_list_command(cmd_args))
    elif cmd == "remove":
        sys.exit(run_remove_command(cmd_args))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print("Run 'as-mcp-cli --help' for usage.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
