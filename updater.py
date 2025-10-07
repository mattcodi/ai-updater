#!/usr/bin/env python3
import os
import sys
import yaml
import zipfile
import httpx
import hashlib
import tempfile
import subprocess
from fastapi import FastAPI, HTTPException
import uvicorn

CONFIG_PATH = "/opt/ai-updater/config.yaml"
PROJECTS = {}


# ---------------------- Grundfunktionen ----------------------
def load_config():
    global PROJECTS
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)
    PROJECTS = cfg.get("projects", {})


def sha256sum(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------- GitHub Repo Management ----------------------
async def create_github_repo(repo_name: str, description: str = "") -> str:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("⚠️ Kein GitHub-Token gefunden. Bitte Umgebungsvariable GITHUB_TOKEN setzen.")

    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    data = {
        "name": repo_name,
        "description": description or f"Auto-created repository for {repo_name}",
        "private": False
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post("https://api.github.com/user/repos", headers=headers, json=data)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"GitHub-API-Fehler ({r.status_code}): {r.text}")
        repo_info = r.json()
        html_url = repo_info["html_url"]
        ssh_url = repo_info["ssh_url"]

        print(f"✅ Repository {html_url} wurde erstellt.")
        print("🔧 Initialisiere lokales Git-Repo und pushe erste Version ...")

        # Automatische Initialisierung und Push
        repo_path = f"/opt/{repo_name}"
        if not os.path.exists(repo_path):
            print(f"⚠️ Pfad {repo_path} existiert nicht – erstelle neues Projektverzeichnis.")
            os.makedirs(repo_path, exist_ok=True)
            open(os.path.join(repo_path, "README.md"), "w").write(f"# {repo_name}\n\nAutomatisch erstellt durch AI-Updater.\n")

        subprocess.run(["git", "init"], cwd=repo_path, check=False)
        subprocess.run(["git", "remote", "add", "origin", ssh_url], cwd=repo_path, check=False)
        subprocess.run(["git", "add", "."], cwd=repo_path, check=False)
        subprocess.run(["git", "commit", "-m", "Initial commit – created by AI-Updater"], cwd=repo_path, check=False)
        subprocess.run(["git", "branch", "-M", "main"], cwd=repo_path, check=False)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo_path, check=False)

        print(f"🚀 Repository {repo_name} erfolgreich initialisiert und gepusht.")
        return html_url


# ---------------------- Update-Logik ----------------------
async def run_update(name: str) -> str:
    if name not in PROJECTS:
        raise ValueError(f"Projekt '{name}' nicht in config.yaml definiert")

    info = PROJECTS[name]
    path = info["path"]
    url = info["update_url"]
    service = info.get("service")
    tmpdir = tempfile.mkdtemp()
    zip_path = os.path.join(tmpdir, "update.zip")

    print(f"🔄 Starte Update für {name} ...")

    # Lokale oder Remote-Datei abrufen
    if url.startswith("/") and os.path.exists(url):
        print(f"📦 Lokales Update gefunden: {url}")
        with open(url, "rb") as src, open(zip_path, "wb") as dst:
            dst.write(src.read())
    else:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                raise RuntimeError(f"Download fehlgeschlagen ({r.status_code})")
            with open(zip_path, "wb") as f:
                f.write(r.content)

    # optional Hashprüfung
    if os.path.exists(zip_path + ".sha256"):
        expected = open(zip_path + ".sha256").read().strip()
        actual = sha256sum(zip_path)
        if expected != actual:
            raise RuntimeError("SHA256 mismatch – Update abgebrochen")

    # Dateien extrahieren, aber venv-Python-Interpreter überspringen
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if "venv/bin/python" in member or "venv/bin/python3" in member:
                continue
            zf.extract(member, path)

    subprocess.run(["git", "add", "."], cwd=path, check=False)
    subprocess.run(["git", "commit", "-m", f"auto-update: {name}"], cwd=path, check=False)
    subprocess.run(["git", "push"], cwd=path, check=False)

    if service:
        subprocess.run(["sudo", "systemctl", "restart", service], check=False)
        print(f"✅ {service} neu gestartet")

    return f"{name} erfolgreich aktualisiert."


# ---------------------- CLI & API ----------------------
def cli():
    if len(sys.argv) < 2:
        print("Usage: updater.py <project_name> | serve | create_repo <repo_name> [description]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "serve":
        load_config()
        app = FastAPI(title="AI-Updater")

        @app.post("/update/{name}")
        async def update_api(name: str):
            try:
                msg = await run_update(name)
                return {"status": "ok", "msg": msg}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/create-repo/{name}")
        async def api_create_repo(name: str, description: str = ""):
            try:
                url = await create_github_repo(name, description)
                return {"status": "ok", "url": url}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        uvicorn.run(app, host="0.0.0.0", port=9010)

    elif cmd == "create_repo":
        if len(sys.argv) < 3:
            print("Usage: updater.py create_repo <repo_name> [description]")
            sys.exit(1)
        repo_name = sys.argv[2]
        description = sys.argv[3] if len(sys.argv) > 3 else ""
        import asyncio
        asyncio.run(create_github_repo(repo_name, description))
        sys.exit(0)

    else:
        load_config()
        import asyncio
        asyncio.run(run_update(cmd))


if __name__ == "__main__":
    cli()
