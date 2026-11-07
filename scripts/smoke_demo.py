"""Exercise the running demo with only the Python standard library."""
import http.cookiejar
import json
import sys
import urllib.request

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
request = urllib.request.Request(base + "/api/auth/login", data=json.dumps({
    "email": "demo@portfolio.local", "password": "Demo@12345"
}).encode(), headers={"Content-Type": "application/json"})
with client.open(request, timeout=15) as response:
    assert response.status == 200
for path in ("/healthz", "/workspace", "/api/auth/me", "/api/vehicles/live", "/api/system-state"):
    with client.open(base + path, timeout=15) as response:
        assert response.status == 200, path
print("Demo startup, login, session, workspace and data: OK")
