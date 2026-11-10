"""Signed, read-only GitHub release checks and staged update downloads."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


REPOSITORY = "LRTTTTTT/wechat-text-export"
PUBLIC_KEY = bytes.fromhex("e21ada04577e75e1fbf5d5d8bcab19a3df308019467e181e2dedf2b8109094e1")
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_MANIFEST = 16_384
MAX_ARCHIVE = 500_000_000


def version_parts(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Invalid version")
    return tuple(int(p) for p in value.split("."))


def _get(url: str, max_bytes: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "WeChatTextExport-Updater"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError("Update asset exceeds size limit")
    return payload


def verify_manifest(raw: bytes, signature_b64: bytes, release_tag: str) -> dict:
    signature = base64.b64decode(signature_b64, validate=True)
    Ed25519PublicKey.from_public_bytes(PUBLIC_KEY).verify(signature, raw)
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("Invalid update manifest")
    version = manifest.get("version")
    if (manifest.get("repository") != REPOSITORY or not isinstance(version, str)
            or release_tag != "v" + version):
        raise ValueError("Update repository or version mismatch")
    version_parts(version)
    archive = manifest.get("archive")
    digest = manifest.get("sha256")
    if archive != f"WeChatTextExport-{version}.zip":
        raise ValueError("Unexpected archive name")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid archive digest")
    return manifest


def latest_release(current_version: str) -> tuple[dict, str] | None:
    release = json.loads(_get(API_URL, 256_000))
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        return None
    tag = release.get("tag_name", "")
    if not isinstance(tag, str) or not tag.startswith("v"):
        return None
    assets = {a.get("name"): a.get("browser_download_url") for a in release.get("assets", [])
              if isinstance(a, dict)}
    if not assets.get("update.json") or not assets.get("update.sig"):
        return None
    raw = _get(assets["update.json"], MAX_MANIFEST)
    sig = _get(assets["update.sig"], 512)
    manifest = verify_manifest(raw, sig, tag)
    if version_parts(manifest["version"]) <= version_parts(current_version):
        return None
    url = assets.get(manifest["archive"])
    if not isinstance(url, str) or not url.startswith(
        f"https://github.com/{REPOSITORY}/releases/download/"
    ):
        raise ValueError("Release archive URL is not in the approved repository")
    return manifest, url


def stage_release(manifest: dict, url: str, local_root: Path) -> Path:
    """Verify the entire archive, then extract to a new immutable version dir."""
    version = manifest["version"]
    versions = local_root / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    target = versions / version
    if target.exists():
        raise FileExistsError("Target version directory already exists")
    pending = versions / f"{version}.pending.{os.getpid()}"
    if pending.exists():
        raise FileExistsError("Update staging directory already exists")
    blob = _get(url, MAX_ARCHIVE)
    if hashlib.sha256(blob).hexdigest() != manifest["sha256"]:
        raise ValueError("Release archive checksum mismatch")
    pending.mkdir()
    try:
        import io

        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for member in archive.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts or ":" in member.filename:
                    raise ValueError("Unsafe path inside update archive")
                destination = pending / path
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        if not (pending / "WeChatTextExport.exe").is_file():
            raise ValueError("Release archive has no program executable")
        os.replace(pending, target)
        return target
    except Exception:
        for item in sorted(pending.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if item.is_dir():
                item.rmdir()
            else:
                item.unlink()
        pending.rmdir()
        raise


def launch_switch(new_version_dir: Path, current_version: str, local_root: Path) -> None:
    """A hidden helper switches the scheduled task after this process exits."""
    helper = Path(sys.executable).resolve().parent / "switch_task.ps1"
    if not helper.is_file():
        raise FileNotFoundError("Update switch helper is missing")
    new_exe = new_version_dir / "WeChatTextExport.exe"
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-ExecutionPolicy", "Bypass", "-File", str(helper), "-OldPid", str(os.getpid()),
         "-NewExe", str(new_exe), "-NewVersion", new_version_dir.name,
         "-OldVersion", current_version, "-LocalRoot", str(local_root)],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
