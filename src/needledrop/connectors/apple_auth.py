"""MusicKit-JS authorization helper: serve a local page that obtains a Music User
Token in the browser and POSTs it back, then persist it to the keystore.

The browser flow itself is a manual/interactive path; the pure pieces (page
rendering, token extraction) are unit-tested, and the live server loop is driven
by `needledrop auth apple login`.
"""

from __future__ import annotations

import http.server
import threading
import webbrowser
from urllib.parse import parse_qs

from needledrop.connectors.apple_token import store_user_token

CALLBACK_PATH = "/callback"

# `developer_token` and `app_name` are interpolated into JS string literals below.
# This is safe: the developer token is an ES256 JWT (base64url + '.', no quotes or
# backslashes) signed from the operator's own .p8, and `app_name` is an operator-
# supplied constant — neither is attacker-controlled, so neither can break out of
# the surrounding '...' literal. The MusicKit <script> deliberately omits a
# Subresource Integrity hash: Apple serves that file mutably/unversioned, so a
# pinned hash would break the auth flow on every Apple update.
_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>NeedleDrop — Apple Music authorization</title></head>
<body>
<h1>Authorizing {app_name} with Apple Music…</h1>
<p id="status">Loading MusicKit…</p>
<button id="authorize" style="display:none;font-size:1rem;padding:0.6rem 1rem;">
  Authorize Apple Music
</button>
<script src="https://js-cdn.music.apple.com/musickit/v3/musickit.js"></script>
<script>
function setStatus(text) {{ document.getElementById('status').textContent = text; }}

async function init() {{
  try {{
    // MusicKit v3 configure() is async — await it before getInstance(), or
    // getInstance() races ahead and throws while the page hangs on "Loading".
    await MusicKit.configure({{
      developerToken: '{developer_token}',
      app: {{ name: '{app_name}', build: '1.0' }},
    }});
  }} catch (err) {{
    setStatus('MusicKit configure failed: ' + err);
    return;
  }}
  // Authorize on an explicit click: browsers block the Apple sign-in popup when
  // authorize() is invoked without a user gesture.
  var button = document.getElementById('authorize');
  button.style.display = 'inline-block';
  setStatus('Ready — click the button to authorize Apple Music.');
  button.addEventListener('click', function () {{
    button.disabled = true;
    setStatus('Opening Apple Music sign-in…');
    MusicKit.getInstance().authorize().then(function (musicUserToken) {{
      setStatus('Authorized. You can close this tab.');
      return fetch('{callback_path}', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
        body: 'musicUserToken=' + encodeURIComponent(musicUserToken),
      }});
    }}).catch(function (err) {{
      button.disabled = false;
      setStatus('Authorization failed: ' + err);
    }});
  }});
}}

// Handle both load orderings: MusicKit may already be present when this runs,
// or it may fire `musickitloaded` afterwards. (Listening only for the event
// misses it if the script finished first.)
if (window.MusicKit) {{ init(); }}
else {{ document.addEventListener('musickitloaded', init); }}
</script>
</body>
</html>
"""


def build_auth_page(developer_token: str, *, app_name: str = "NeedleDrop") -> str:
    """Render the local MusicKit-JS authorization page."""
    return _PAGE_TEMPLATE.format(
        developer_token=developer_token, app_name=app_name, callback_path=CALLBACK_PATH
    )


def extract_user_token(form_body: str) -> str:
    """Pull the `musicUserToken` value out of a urlencoded form body."""
    values = parse_qs(form_body).get("musicUserToken")
    if not values:
        raise ValueError("musicUserToken not present in callback body")
    return values[0]


def run_auth_helper(
    developer_token: str,
    *,
    port: int,
    app_name: str = "NeedleDrop",
    open_browser: bool = True,
    timeout: float = 300.0,
) -> str:
    """Serve the auth page on localhost, capture the posted Music User Token,
    persist it, and return it. Manual/interactive path (not unit-tested)."""
    page = build_auth_page(developer_token, app_name=app_name)
    captured: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default logging
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode("utf-8"))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                token = extract_user_token(body)
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            captured["token"] = token
            store_user_token(token)
            self.send_response(204)
            self.end_headers()
            done.set()

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{port}/"
        if open_browser:
            webbrowser.open(url)
        if not done.wait(timeout=timeout):
            raise TimeoutError("Timed out waiting for Apple Music authorization")
        return captured["token"]
    finally:
        server.shutdown()
        server.server_close()
