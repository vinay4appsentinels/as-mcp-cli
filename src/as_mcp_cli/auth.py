#!/usr/bin/env python3
"""
OAuth authentication for MCP servers using device code flow.
"""

import json
import sys
import time
import hashlib
import base64
import secrets
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
import requests
import threading


CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
DEFAULT_SCOPES = "openid profile email offline_access"


def get_well_known_config(server_url):
    """Fetch OAuth configuration from well-known endpoint"""
    parsed = urlparse(server_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Try standard MCP well-known path first
    well_known_url = f"{base_url}/.well-known/oauth-authorization-server"

    try:
        resp = requests.get(well_known_url, timeout=10)
        if resp.status_code == 200:
            return resp.json(), base_url
    except requests.RequestException:
        pass

    # Try with /mcp prefix
    well_known_url = f"{base_url}/mcp/.well-known/oauth-authorization-server"
    try:
        resp = requests.get(well_known_url, timeout=10)
        if resp.status_code == 200:
            return resp.json(), base_url
    except requests.RequestException:
        pass

    return None, base_url


def generate_pkce():
    """Generate PKCE code verifier and challenge"""
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    return code_verifier, code_challenge


def generate_state():
    """Generate random state for OAuth"""
    return secrets.token_urlsafe(16)


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler to receive OAuth callback"""

    def log_message(self, format, *args):
        pass  # Suppress logging

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            self.server.auth_code = params["code"][0]
            self.server.auth_state = params.get("state", [None])[0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1>Authentication Successful!</h1>
                <p>You can close this window and return to the terminal.</p>
                </body></html>
            """)
        elif "error" in params:
            self.server.auth_error = params.get("error_description", params["error"])[0]
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            error_msg = params.get("error_description", params["error"])[0]
            self.wfile.write(f"""
                <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                <h1>Authentication Failed</h1>
                <p>{error_msg}</p>
                </body></html>
            """.encode())
        else:
            self.send_response(404)
            self.end_headers()


def auth_with_browser(oauth_config, client_id, base_url, server_name, server_url):
    """Authenticate using browser-based OAuth flow with PKCE"""

    auth_endpoint = oauth_config["authorization_endpoint"]
    token_endpoint = oauth_config["token_endpoint"]

    # Generate PKCE
    code_verifier, code_challenge = generate_pkce()
    state = generate_state()

    # Start local callback server
    callback_port = 8585
    redirect_uri = f"http://localhost:{callback_port}/callback"

    server = HTTPServer(("localhost", callback_port), CallbackHandler)
    server.auth_code = None
    server.auth_state = None
    server.auth_error = None
    server.timeout = 120

    # Build authorization URL
    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": DEFAULT_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{auth_endpoint}?{urlencode(auth_params)}"

    print(f"Opening browser for authentication...")
    print(f"If browser doesn't open, visit: {auth_url}")

    # Open browser
    webbrowser.open(auth_url)

    # Wait for callback
    print("Waiting for authentication...")

    while server.auth_code is None and server.auth_error is None:
        server.handle_request()

    if server.auth_error:
        print(f"Authentication failed: {server.auth_error}", file=sys.stderr)
        return None

    if server.auth_state != state:
        print("Authentication failed: state mismatch", file=sys.stderr)
        return None

    # Exchange code for tokens
    print("Exchanging code for tokens...")

    token_data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": server.auth_code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }

    try:
        resp = requests.post(token_endpoint, data=token_data, timeout=30)
        resp.raise_for_status()
        tokens = resp.json()
    except requests.RequestException as e:
        print(f"Token exchange failed: {e}", file=sys.stderr)
        return None

    # Calculate expiry
    expires_in = tokens.get("expires_in", 3600)
    expires_at = int(time.time() * 1000) + (expires_in * 1000)

    return {
        "serverName": server_name,
        "serverUrl": server_url,
        "clientId": client_id,
        "accessToken": tokens["access_token"],
        "expiresAt": expires_at,
        "refreshToken": tokens.get("refresh_token", ""),
        "scope": tokens.get("scope", DEFAULT_SCOPES),
    }


def refresh_token(cred_entry, oauth_config):
    """Refresh an expired access token"""

    if not cred_entry.get("refreshToken"):
        return None

    token_endpoint = oauth_config["token_endpoint"]

    token_data = {
        "grant_type": "refresh_token",
        "client_id": cred_entry["clientId"],
        "refresh_token": cred_entry["refreshToken"],
    }

    try:
        resp = requests.post(token_endpoint, data=token_data, timeout=30)
        resp.raise_for_status()
        tokens = resp.json()
    except requests.RequestException as e:
        print(f"Token refresh failed: {e}", file=sys.stderr)
        return None

    expires_in = tokens.get("expires_in", 3600)
    expires_at = int(time.time() * 1000) + (expires_in * 1000)

    cred_entry["accessToken"] = tokens["access_token"]
    cred_entry["expiresAt"] = expires_at
    if "refresh_token" in tokens:
        cred_entry["refreshToken"] = tokens["refresh_token"]

    return cred_entry


def save_credentials(server_name, server_url, cred_entry):
    """Save credentials to ~/.claude/.credentials.json"""

    # Ensure directory exists
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load existing credentials
    if CREDENTIALS_PATH.exists():
        with open(CREDENTIALS_PATH) as f:
            creds = json.load(f)
    else:
        creds = {}

    if "mcpOAuth" not in creds:
        creds["mcpOAuth"] = {}

    # Generate key similar to Claude's format
    parsed = urlparse(server_url)
    url_hash = hashlib.md5(f"{parsed.netloc}{parsed.path}".encode()).hexdigest()[:16]
    key = f"{server_name}|{url_hash}"

    creds["mcpOAuth"][key] = cred_entry

    # Save
    with open(CREDENTIALS_PATH, "w") as f:
        json.dump(creds, f, indent=2)

    print(f"Credentials saved to {CREDENTIALS_PATH}")


def get_existing_credential(mcp_name):
    """Get existing credential entry for an MCP server"""
    if not CREDENTIALS_PATH.exists():
        return None, None

    with open(CREDENTIALS_PATH) as f:
        creds = json.load(f)

    mcp_oauth = creds.get("mcpOAuth", {})

    for key, value in mcp_oauth.items():
        if value.get("serverName") == mcp_name:
            return key, value

    return None, None


def run_auth(mcp_name=None, server_url=None, client_id=None, force=False):
    """Run authentication flow for an MCP server

    Args:
        mcp_name: Name of the MCP server (required)
        server_url: Server URL (optional, will use existing if available)
        client_id: OAuth client ID (optional, will try to discover or use existing)
        force: Force re-authentication even if valid token exists

    Returns:
        0 on success, 1 on failure
    """

    if not mcp_name:
        print("Error: MCP server name is required", file=sys.stderr)
        return 1

    # Check for existing credentials
    existing_key, existing_cred = get_existing_credential(mcp_name)

    if existing_cred and not force:
        # Check if token is still valid (with 5 min buffer)
        expires_at = existing_cred.get("expiresAt", 0)
        if expires_at > (time.time() * 1000) + 300000:
            print(f"Valid token exists for '{mcp_name}'. Use --force to re-authenticate.")
            return 0

        # Try to refresh
        if existing_cred.get("refreshToken"):
            print(f"Token expired, attempting refresh...")
            oauth_config, base_url = get_well_known_config(existing_cred["serverUrl"])
            if oauth_config:
                refreshed = refresh_token(existing_cred, oauth_config)
                if refreshed:
                    save_credentials(mcp_name, existing_cred["serverUrl"], refreshed)
                    print("Token refreshed successfully!")
                    return 0
            print("Refresh failed, proceeding with full authentication...")

    # Determine server URL
    if not server_url:
        if existing_cred:
            server_url = existing_cred["serverUrl"]
        else:
            print("Error: --server-url is required for new MCP servers", file=sys.stderr)
            return 1

    # Fetch OAuth configuration
    oauth_config, base_url = get_well_known_config(server_url)

    if not oauth_config:
        print(f"Error: Could not fetch OAuth configuration from {server_url}", file=sys.stderr)
        return 1

    # Determine client ID
    if not client_id:
        if existing_cred:
            client_id = existing_cred["clientId"]
        else:
            # Try dynamic client registration
            if "registration_endpoint" in oauth_config:
                print("Attempting dynamic client registration...")
                try:
                    reg_data = {
                        "client_name": "as-mcp-cli",
                        "redirect_uris": ["http://localhost:8585/callback"],
                        "grant_types": ["authorization_code", "refresh_token"],
                        "response_types": ["code"],
                        "token_endpoint_auth_method": "none",
                    }
                    resp = requests.post(
                        oauth_config["registration_endpoint"],
                        json=reg_data,
                        timeout=30
                    )
                    if resp.status_code in (200, 201):
                        reg_result = resp.json()
                        client_id = reg_result["client_id"]
                        print(f"Registered client: {client_id}")
                except requests.RequestException as e:
                    print(f"Dynamic registration failed: {e}", file=sys.stderr)

            if not client_id:
                print("Error: --client-id is required (dynamic registration failed)", file=sys.stderr)
                return 1

    # Run browser-based auth
    cred_entry = auth_with_browser(oauth_config, client_id, base_url, mcp_name, server_url)

    if not cred_entry:
        return 1

    # Save credentials
    save_credentials(mcp_name, server_url, cred_entry)
    print(f"Authentication successful for '{mcp_name}'!")

    return 0
