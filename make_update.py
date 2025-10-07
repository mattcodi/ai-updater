#!/usr/bin/env python3
import os
import zipfile
import yaml
import datetime
import subprocess
import httpx

CONFIG_PATH = "/opt/ai-updater/config.yaml"
BASE_UPDATE_DIR = "/opt/ai-updater/updates"

EXCLUDE_DIRS = {".git", "venv", "__pycache__", ".mypy_cache", ".idea", ".vscode", "logs"}
EXCLUDE_FILES = {".DS_Store", ".env", ".python-version"}
INCLUDE_ALWAYS = {"/opt/ai-updater/DEVELOPMENT_GUIDE.md"}


# ---------------------- Hilfsfunktionen ----------------------
def load_projects():
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("projects", {})


def zip_project(name, path):
    os.makedirs(os.path.join(BASE_UPDATE_DIR, name), exist_ok=True)
    version_tag = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = os.path.join(BASE_UPDATE_DIR, name, f"{version_tag}.zip")
    latest_path = os.path.join(BASE_UPDATE_DIR, name, "latest.zip")

    print(f"📦 Erstelle Update-Paket für {name} aus {path}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in files:
                if file in EXCLUDE_FILES:
                    continue
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, path)
                zipf.write(abs_path, rel_path)

        # Immer zusätzliche Dateien mit einpacken
        for extra_file in INCLUDE_ALWAYS:
            if os.path.exists(extra_file):
                rel_name = os.path.basename(extra_file)
                zipf.write(extra_file, rel_name)

    if os.path.exists(latest_path):
        os.remove(latest_path)
    os.link(zip_path, latest_path)

    print(f"✅ {name}: {zip_path} erstellt und als latest.zip verlinkt")
    return version_tag, zip_path


def git_versioning(name, path, version_tag):
    print(f"🔧 Versioniere Git-Repo {path} ...")
    try:
        subprocess.run(["git", "fetch"], cwd=path, check=False)
        subprocess.run(["git", "pull", "--rebase"], cwd=path, check=False)
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        commit_msg = f"auto-build: {name} {version_tag}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=path, check=False)
        tag_name = f"v{version_tag}-{name}"
        subprocess.run(["git", "tag", "-a", tag_name, "-m", f"Build {version_tag}"], cwd=path, check=False)
        subprocess.run(["git", "push"], cwd=path, check=False)
        subprocess.run(["git", "push", "--tags"], cwd=path, check=False)
        print(f"🏷️  Git-Version {tag_name} erstellt, gepusht und synchronisiert.")
    except Exception as e:
        print(f"⚠️  Git-Versionierung für {name} fehlgeschlagen: {e}")


# ---------------------- GitHub Release Upload ----------------------
def upload_release_to_github(repo_name: str, version_tag: str, zip_path: str):
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("⚠️ Kein GitHub-Token gesetzt – überspringe Release-Upload.")
        return

    owner = "mattcodi"  # GitHub Benutzername
    api_base = f"https://api.github.com/repos/{owner}/{repo_name}"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json"
    }

    try:
        # Release erstellen oder aktualisieren
        release_data = {
            "tag_name": version_tag,
            "name": f"{repo_name} {version_tag}",
            "body": f"Automatisch erstellt durch AI-Updater\n\nBuild: {version_tag}",
            "draft": False,
            "prerelease": False
        }

        r = httpx.post(f"{api_base}/releases", headers=headers, json=release_data)
        if r.status_code == 422 and "already_exists" in r.text:
            print(f"ℹ️ Release {version_tag} existiert bereits – hole Release-ID ...")
            releases = httpx.get(f"{api_base}/releases", headers=headers).json()
            release_id = next((rel["id"] for rel in releases if rel["tag_name"] == version_tag), None)
        elif r.status_code in (200, 201):
            release_id = r.json()["id"]
        else:
            print(f"⚠️ Fehler beim Erstellen des Releases ({r.status_code}): {r.text}")
            return

        # Asset hochladen
        upload_url = f"https://uploads.github.com/repos/{owner}/{repo_name}/releases/{release_id}/assets?name={os.path.basename(zip_path)}"
        headers["Content-Type"] = "application/zip"

        with open(zip_path, "rb") as f:
            ur = httpx.post(upload_url, headers=headers, content=f.read())

        if ur.status_code in (200, 201):
            print(f"🚀 Release-Asset {os.path.basename(zip_path)} erfolgreich zu {repo_name} hochgeladen.")
        else:
            print(f"⚠️ Upload-Fehler ({ur.status_code}): {ur.text}")

    except Exception as e:
        print(f"⚠️ Release-Upload fehlgeschlagen: {e}")


# ---------------------- Hauptablauf ----------------------
def main():
    projects = load_projects()
    if not projects:
        print("❌ Keine Projekte in config.yaml gefunden.")
        return

    for name, info in projects.items():
        path = info["path"]
        if not os.path.exists(path):
            print(f"⚠️  Pfad {path} existiert nicht, überspringe {name}")
            continue

        version_tag, zip_path = zip_project(name, path)
        git_versioning(name, path, version_tag)
        upload_release_to_github(name, version_tag, zip_path)


if __name__ == "__main__":
    main()

