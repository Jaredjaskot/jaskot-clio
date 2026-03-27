"""
OAuth setup flow for Clio API.

Usage:
    python -m jaskot_clio.auth \
        --client-id YOUR_CLIENT_ID \
        --client-secret YOUR_CLIENT_SECRET \
        --token-file clio_tokens.json
"""

import argparse
import http.server
import threading
import urllib.parse
import webbrowser

from jaskot_clio.client import ClioClient


def run_oauth_flow(
    client_id: str,
    client_secret: str,
    redirect_uri: str = "http://127.0.0.1:9876/callback",
    token_file: str = "clio_tokens.json",
) -> ClioClient:
    """Run the interactive OAuth flow and return an authenticated client."""
    client = ClioClient(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        token_file=token_file,
    )

    parsed = urllib.parse.urlparse(redirect_uri)
    port = parsed.port or 9876

    auth_code = None
    event = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            auth_code = params.get("code", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if auth_code:
                self.wfile.write(b"<h1>Authorization successful!</h1><p>You can close this tab.</p>")
            else:
                self.wfile.write(b"<h1>Authorization failed.</h1>")
            event.set()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    auth_url = client.get_auth_url()
    print(f"\nOpening browser for Clio authorization...\n{auth_url}\n")
    webbrowser.open(auth_url)

    event.wait(timeout=120)
    server.server_close()

    if not auth_code:
        raise RuntimeError("No authorization code received")

    client.exchange_code(auth_code)
    print(f"Tokens saved to {token_file}")
    return client


def main():
    parser = argparse.ArgumentParser(description="Clio OAuth setup")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument("--redirect-uri", default="http://127.0.0.1:9876/callback")
    parser.add_argument("--token-file", default="clio_tokens.json")
    args = parser.parse_args()

    run_oauth_flow(
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
        token_file=args.token_file,
    )


if __name__ == "__main__":
    main()
