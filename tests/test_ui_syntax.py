"""Parse shipped and rendered JavaScript to catch errors outside unit-test slices."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.settings import settings

class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.active = False
    def handle_starttag(self, tag, attrs):
        if tag == "script":
            props = dict(attrs)
            self.active = "src" not in props and props.get("type", "text/javascript") in ("text/javascript", "module")
    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False
    def handle_data(self, text):
        if self.active:
            self.scripts.append(text)

@pytest.mark.skipif(not shutil.which("node"), reason="Node.js required for JavaScript syntax validation")
def test_all_static_and_rendered_javascript_parses(tmp_path):
    scripts = {str(path): path.read_text(encoding="utf8") for path in Path("assets").glob("*.js")}
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"email": settings.demo_email, "password": settings.demo_password})
        for route in ["/", "/login", "/workspace", "/app", "/agenda-tablet"]:
            response = client.get(route)
            assert response.status_code == 200
            parser = InlineScripts()
            parser.feed(response.text)
            scripts.update({route+str(i): text for i, text in enumerate(parser.scripts)})
    for i, (name, text) in enumerate(scripts.items()):
        target = tmp_path / f"script-{i}.js"
        target.write_text(text, encoding="utf8")
        result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
        assert result.returncode == 0, f"{name}: {result.stderr}"
