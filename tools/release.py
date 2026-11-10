"""Build and sign a release; the private key never enters the repository."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import win32crypt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechat_text_export import APP_VERSION  # noqa: E402
from updates import PUBLIC_KEY, REPOSITORY  # noqa: E402

PUBLISHER_DIR = Path.home() / "Documents" / "WeChatTextExportPublisher"
PRIVATE_KEY_FILE = PUBLISHER_DIR / "signing-key.dpapi"


def load_private_key() -> Ed25519PrivateKey:
    raw = PRIVATE_KEY_FILE.read_bytes()
    if not raw.startswith(b"dpapi1:"):
        raise ValueError("Signing key is not DPAPI protected")
    blob = base64.b64decode(raw[7:], validate=True)
    _description, material = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    key = Ed25519PrivateKey.from_private_bytes(material)
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if public != PUBLIC_KEY:
        raise ValueError("Signing key does not match the embedded public key")
    return key


def build_assets() -> Path:
    source = ROOT / "dist" / "WeChatTextExport"
    if not (source / "WeChatTextExport.exe").is_file():
        raise FileNotFoundError("Build the frozen program before signing")
    shutil.copy2(ROOT / "switch_task.ps1", source / "switch_task.ps1")
    shutil.copy2(ROOT / "vendor" / "LICENSE.wechatauto-replica", source / "LICENSE.wechatauto-replica")
    release_dir = ROOT / "release" / f"v{APP_VERSION}"
    release_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"WeChatTextExport-{APP_VERSION}.zip"
    archive_path = release_dir / archive_name
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zip_out:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zip_out.write(path, path.relative_to(source).as_posix())
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest = {
        "archive": archive_name,
        "repository": REPOSITORY,
        "sha256": digest,
        "version": APP_VERSION,
    }
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = load_private_key().sign(raw)
    (release_dir / "update.json").write_bytes(raw)
    (release_dir / "update.sig").write_bytes(base64.b64encode(signature))
    return release_dir


def backup_key(destination: Path) -> Path:
    password = getpass.getpass("New offline backup password: ").encode("utf-8")
    verify = getpass.getpass("Repeat offline backup password: ").encode("utf-8")
    if len(password) < 16 or password != verify:
        raise ValueError("Backup password must match and contain at least 16 characters")
    if destination.exists():
        raise FileExistsError("Refusing to overwrite an existing backup")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encrypted = load_private_key().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password),
    )
    destination.write_bytes(encrypted)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "backup"])
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.action == "build":
        print(build_assets())
    else:
        if args.destination is None:
            parser.error("--destination is required for backup")
        print(backup_key(args.destination))


if __name__ == "__main__":
    main()
