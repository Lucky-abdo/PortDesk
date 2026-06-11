# ── pd_app — singleton Server instance (refactor) ───────────────────────────
# Extracted so route modules can import `app` without circular dependency.
# The Server object is created once here; both portdesk_server.py and
# pd_routes.py reference the SAME instance.
from portdesk_http import Server
app = Server()
