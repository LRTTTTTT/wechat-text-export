import base64
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import updates
import wechat_text_export as app


class UpdateTests(unittest.TestCase):
    def test_manifest_signature_and_tampering(self):
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        manifest = {"version": "0.3.0", "repository": updates.REPOSITORY,
                    "archive": "WeChatTextExport-0.3.0.zip", "sha256": "a" * 64}
        raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        sig = base64.b64encode(key.sign(raw))
        with patch.object(updates, "PUBLIC_KEY", public):
            self.assertEqual(updates.verify_manifest(raw, sig, "v0.3.0"), manifest)
            with self.assertRaises(Exception):
                updates.verify_manifest(raw.replace(b"0.3.0", b"0.4.0"), sig, "v0.4.0")
            with self.assertRaises(ValueError):
                updates.verify_manifest(raw, sig, "v0.4.0")

    def test_stage_archive_checksum_and_path_validation(self):
        def archive(name):
            memory = io.BytesIO()
            with zipfile.ZipFile(memory, "w") as zip_out:
                zip_out.writestr(name, b"test executable")
            return memory.getvalue()

        valid = archive("WeChatTextExport.exe")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = {"version": "0.3.0", "sha256": hashlib.sha256(valid).hexdigest()}
            with patch.object(updates, "_get", return_value=valid):
                target = updates.stage_release(manifest, "https://example.test/archive", root)
            self.assertEqual((target / "WeChatTextExport.exe").read_bytes(), b"test executable")
            bad = archive("../escape.txt")
            manifest["version"] = "0.4.0"
            manifest["sha256"] = hashlib.sha256(bad).hexdigest()
            with patch.object(updates, "_get", return_value=bad), self.assertRaises(ValueError):
                updates.stage_release(manifest, "https://example.test/archive", root)
            self.assertFalse((root / "escape.txt").exists())

    def test_portable_data_root_and_output_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "xwechat_files"
            (root / "account" / "db_storage").mkdir(parents=True)
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"data_root": str(root)}), encoding="utf-8")
            with patch.object(app, "CONFIG_FILE", config), patch.dict("os.environ", {"WECHAT_TEXT_DATA_ROOT": ""}):
                self.assertEqual(app.refresh_data_root(), root)
                self.assertEqual(app.OUTPUT_ROOT, root / "txt_chat")


if __name__ == "__main__":
    unittest.main()
