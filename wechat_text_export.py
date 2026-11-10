"""Quiet, read-only Weixin 4.2 text archive for the current Windows user."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import winreg
from pathlib import Path

import psutil
import ntsecuritycon
import win32crypt  # required: upstream must never fall back to plaintext key storage
import win32api
import win32con
import win32security


SOURCE_HOME = Path(__file__).resolve().parent
HERE = Path(getattr(sys, "_MEIPASS", SOURCE_HOME))
APP_VERSION = "0.2.0"
VENDOR_DB = HERE / "vendor" / "wechatauto" / "db.py"
VENDOR_SHA256 = "c39b12ad20db284db125f045e398b58b11d2901a52bad857178b44c00a5c0155"
if hashlib.sha256(VENDOR_DB.read_bytes()).hexdigest() != VENDOR_SHA256:
    raise RuntimeError("Vendored database reader changed; review it before running")
sys.path.insert(0, str(HERE / "vendor"))
from wechatauto.db import WeChatDB, _verify_enc_key  # noqa: E402


LOCAL_ROOT = Path(os.environ.get("LOCALAPPDATA", str(HERE))) / "WeChatTextExport"
APPDATA = Path(os.environ.get("APPDATA", str(HERE)))
CONFIG_FILE = LOCAL_ROOT / "config.json"
LOG_FILE = LOCAL_ROOT / "export.log"
DATA_ROOT: Path | None = None
OUTPUT_ROOT: Path | None = None
STATE_ROOT: Path | None = None
POLL_SECONDS = 5
HEARTBEAT_SECONDS = 120
RETRY_SECONDS = 60


class _NullWriter:
    def write(self, value):
        return len(value)

    def flush(self):
        pass


@contextlib.contextmanager
def silent_upstream():
    sink = _NullWriter()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        yield


def log(event: str, **fields: object) -> None:
    """Only fixed event names and counts/IDs enter the log; never message text."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    parts = [dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), event]
    parts.extend(f"{key}={value}" for key, value in fields.items())
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(" ".join(parts) + "\n")


def _account_root(path: Path) -> bool:
    try:
        return path.is_dir() and any(
            child.is_dir() and (child / "db_storage").is_dir()
            for child in path.iterdir()
        )
    except OSError:
        return False


def _configured_data_root() -> Path | None:
    try:
        configured = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        raw = configured.get("data_root", "")
        if isinstance(raw, str) and raw:
            return Path(raw)
    except (OSError, ValueError, TypeError):
        pass
    return None


def refresh_data_root() -> Path | None:
    """Find this PC's Weixin account root; retry later on a fresh PC."""
    global DATA_ROOT, OUTPUT_ROOT, STATE_ROOT
    override = os.environ.get("WECHAT_TEXT_DATA_ROOT")
    candidates = [Path(override)] if override else []
    configured = _configured_data_root()
    if configured is not None:
        candidates.append(configured)
    for candidate in candidates:
        if _account_root(candidate):
            DATA_ROOT = candidate.resolve()
            OUTPUT_ROOT = DATA_ROOT / "txt_chat"
            STATE_ROOT = OUTPUT_ROOT / "_state"
            return DATA_ROOT
    from wechatauto.db import auto_detect_db_dir
    auto = auto_detect_db_dir()
    if auto and _account_root(Path(auto)):
        DATA_ROOT = Path(auto).resolve()
        OUTPUT_ROOT = DATA_ROOT / "txt_chat"
        STATE_ROOT = OUTPUT_ROOT / "_state"
        return DATA_ROOT
    DATA_ROOT = OUTPUT_ROOT = STATE_ROOT = None
    return None


def save_data_root(path: Path) -> None:
    """Installer/repair can store one manually chosen account root."""
    if not _account_root(path):
        raise ValueError("The selected folder has no Weixin account db_storage directory")
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    pending = CONFIG_FILE.with_suffix(".pending")
    pending.write_text(json.dumps({"data_root": str(path.resolve())}, ensure_ascii=False), encoding="utf-8")
    os.replace(pending, CONFIG_FILE)
    refresh_data_root()


def _installed_weixin_paths() -> list[Path]:
    found = []
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            if (proc.info["name"] or "").lower() == "weixin.exe" and proc.info["exe"]:
                found.append(Path(proc.info["exe"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            found.append(Path(base) / "Tencent" / "Weixin" / "Weixin.exe")
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for subkey in (
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Weixin",
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Weixin",
        ):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    location = winreg.QueryValueEx(key, "InstallLocation")[0]
                    found.append(Path(location) / "Weixin.exe")
            except OSError:
                pass
    return list(dict.fromkeys(path for path in found if path.is_file()))


def weixin_versions() -> list[str]:
    versions = []
    for path in _installed_weixin_paths():
        try:
            info = win32api.GetFileVersionInfo(str(path), "\\")
            ms, ls = info["FileVersionMS"], info["FileVersionLS"]
            versions.append(f"{ms >> 16}.{ms & 0xffff}.{ls >> 16}.{ls & 0xffff}")
        except (OSError, TypeError, KeyError):
            continue
    return sorted(set(versions))


def version_supported() -> bool:
    versions = weixin_versions()
    return not versions or all(v.startswith("4.") for v in versions)


def repair_data_root() -> int:
    """Only this explicit installer/repair mode may open a folder picker."""
    if len(sys.argv) > 2:
        chosen = Path(sys.argv[2])
    else:
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = tk.Tk()
        root.withdraw()
        selected = filedialog.askdirectory(title="选择包含微信账号目录的 xwechat_files 文件夹")
        root.destroy()
        if not selected:
            return 1
        chosen = Path(selected)
    try:
        save_data_root(chosen)
    except (OSError, ValueError):
        log("invalid_manual_data_root")
        return 1
    log("manual_data_root_saved")
    return 0


def secure_output_directory(path: Path) -> None:
    """Protect plaintext exports with a DACL for this user, SYSTEM and admins."""
    path.mkdir(parents=True, exist_ok=True)
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    admins_sid = win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid, None)
    system_sid = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
    acl = win32security.ACL()
    inheritance = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE
    for sid in (user_sid, admins_sid, system_sid):
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, inheritance, ntsecuritycon.FILE_ALL_ACCESS, sid)
    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    win32security.SetFileSecurity(
        str(path),
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )


def account_dirs() -> list[Path]:
    if DATA_ROOT is None or not DATA_ROOT.is_dir():
        return []
    return sorted(p for p in DATA_ROOT.iterdir() if p.is_dir() and (p / "db_storage").is_dir())


def process_signature() -> tuple[tuple[int, float], ...]:
    found = []
    for proc in psutil.process_iter(["name", "create_time"]):
        try:
            if (proc.info["name"] or "").lower() == "weixin.exe":
                found.append((proc.pid, float(proc.info["create_time"])))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return tuple(sorted(found))


def login_marker(account: str) -> tuple[int, int]:
    """Two login-specific files, plus directory creation, cover in-process relogin."""
    wxid = re.sub(r"_\w{4}$", "", account)
    assert DATA_ROOT is not None
    paths = [
        APPDATA / "Tencent" / "xwechat" / "login" / wxid / "key_info.dat",
        DATA_ROOT / "all_users" / "login" / wxid / "key_info.db",
    ]
    values = []
    for path in paths:
        try:
            values.append(path.stat().st_mtime_ns)
        except OSError:
            values.append(0)
    return values[0], values[1]


def live_accounts() -> list[str]:
    """Match keys from *live process memory* to DB page one; cached keys do not count."""
    if DATA_ROOT is None or not process_signature():
        return []
    probe = object.__new__(WeChatDB)
    probe.db_dir = str(DATA_ROOT)
    with silent_upstream():
        candidates = probe._all_key_candidates()
    matches = []
    for directory in account_dirs():
        sample = directory / "db_storage" / "contact" / "contact.db"
        if not sample.is_file():
            continue
        try:
            with sample.open("rb") as f:
                page = f.read(4096)
        except OSError:
            continue
        if any(_verify_enc_key(key, page, salt=salt) for key, salt in candidates):
            matches.append(directory.name)
    return matches


class ReadOnlyDB(WeChatDB):
    """Pin the account chosen by the live-key probe; forbid upstream auto switching."""

    def _try_other_accounts(self) -> bool:
        return False

    def _select_account_by_keys(self, candidates) -> bool:
        return False


def safe_filename(display_name: str, chat_id: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", display_name or "会话")
    name = name.strip(" .")[:60] or "会话"
    suffix = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:10]
    return f"{name}__{suffix}.txt"


def allowed_chat(user: str, verify_flag: int | None) -> bool:
    if user == "filehelper" or user.endswith("@chatroom"):
        return True
    if not user or re.fullmatch(r"[0-9a-f]{32}", user):
        return False  # unresolved conversation cannot be classified safely
    if user.startswith("gh_") or (int(verify_flag or 0) & 8):
        return False
    return True


def message_body(type_code: int, content: object, compressed: object = None) -> str | None:
    code = int(type_code) & 0xFFFFFFFF
    if code == 10000:
        return None  # system notices are outside the requested chat scope
    if code == 1:
        for raw in (content, compressed):
            if isinstance(raw, bytes):
                text = WeChatDB._friendly_content(raw, "文本")
            elif isinstance(raw, str):
                text = raw
            else:
                text = ""
            if text and text != "[文本]":
                return text.replace("\r\n", "\n").replace("\r", "\n")
        return "[无法解析的文字]"
    return {
        3: "[图片]",
        34: "[语音]",
        43: "[视频]",
        47: "[表情]",
        48: "[位置]",
        49: "[卡片]",
        50: "[音视频通话]",
        11000: "[表情]",
    }.get(code, "[其他消息]")


def msg_identity(server_id: object, shard: str, local_id: object) -> str:
    try:
        if int(server_id or 0) > 0:
            return f"s:{int(server_id)}"
    except (TypeError, ValueError):
        pass
    return f"l:{shard}:{local_id}"


def timestamp(value: object) -> int:
    stamp = int(value or 0)
    return stamp // 1000 if stamp > 10_000_000_000 else stamp


class Archive:
    def __init__(self, account: str):
        assert OUTPUT_ROOT is not None
        self.folder = OUTPUT_ROOT / account
        secure_output_directory(self.folder)
        self.conn = sqlite3.connect(self.folder / "_index.sqlite")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS chats ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, filename TEXT NOT NULL UNIQUE, dirty INTEGER NOT NULL DEFAULT 1);"
            "CREATE TABLE IF NOT EXISTS messages ("
            "chat_id TEXT NOT NULL, msg_id TEXT NOT NULL, ts INTEGER NOT NULL, seq INTEGER NOT NULL, "
            "sender TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(chat_id,msg_id));"
            "CREATE INDEX IF NOT EXISTS messages_order ON messages(chat_id,ts,seq,msg_id);"
        )

    def close(self) -> None:
        self.conn.close()

    def ensure_chat(self, chat_id: str, name: str) -> None:
        filename = safe_filename(name, chat_id)
        self.conn.execute(
            "INSERT OR IGNORE INTO chats(id,name,filename,dirty) VALUES(?,?,?,1)",
            (chat_id, name, filename),
        )
        self.conn.execute("UPDATE chats SET name=? WHERE id=?", (name, chat_id))
        row = self.conn.execute("SELECT filename FROM chats WHERE id=?", (chat_id,)).fetchone()
        if row and not (self.folder / row[0]).exists():
            self.conn.execute("UPDATE chats SET dirty=1 WHERE id=?", (chat_id,))
        self.conn.commit()

    def add_batch(self, chat_id: str, rows: list[tuple]) -> int:
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO messages(chat_id,msg_id,ts,seq,sender,body) VALUES(?,?,?,?,?,?)",
            [(chat_id, *r) for r in rows],
        )
        inserted = self.conn.total_changes - before
        if inserted:
            self.conn.execute("UPDATE chats SET dirty=1 WHERE id=?", (chat_id,))
        self.conn.commit()
        return inserted

    def render_dirty(self) -> int:
        count = 0
        for chat_id, filename in self.conn.execute("SELECT id,filename FROM chats WHERE dirty=1"):
            target = self.folder / filename
            pending = target.with_name(target.name + ".pending")
            try:
                with pending.open("w", encoding="utf-8", newline="\n") as f:
                    for ts, sender, body in self.conn.execute(
                        "SELECT ts,sender,body FROM messages WHERE chat_id=? ORDER BY ts,seq,msg_id",
                        (chat_id,),
                    ):
                        when = dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                        lines = body.split("\n")
                        f.write(f"[{when}] {sender}：{lines[0]}\n")
                        for line in lines[1:]:
                            f.write("    " + line + "\n")
                        f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(pending, target)
                self.conn.execute("UPDATE chats SET dirty=0 WHERE id=?", (chat_id,))
                self.conn.commit()
                count += 1
            finally:
                pending.unlink(missing_ok=True)
        return count


def _temp_dir(account: str) -> Path:
    base = LOCAL_ROOT / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return base / account


def _cleanup_temp(path: Path) -> None:
    base = LOCAL_ROOT / "tmp"
    if path.parent != base or path.is_symlink():
        raise RuntimeError("Refusing to remove a path outside the app temp directory")
    if not path.exists():
        return
    # Upstream writes only flat files. Do not recursively remove an unexpected tree.
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            raise RuntimeError("Unexpected directory in decrypted snapshot cache")
        child.unlink()
    path.rmdir()


def export_account(account: str) -> dict[str, int]:
    assert DATA_ROOT is not None and OUTPUT_ROOT is not None and STATE_ROOT is not None
    secure_output_directory(OUTPUT_ROOT)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ["WECHATAUTO_KEYS_DIR"] = str(STATE_ROOT / "keys")
    workdir = _temp_dir(account)
    workdir.mkdir(parents=True, exist_ok=True)
    archive = None
    conns = []
    stats = {"chats": 0, "new_messages": 0, "files": 0, "skipped": 0}
    try:
        with silent_upstream():
            db = ReadOnlyDB(db_dir=str(DATA_ROOT), account=account, workdir=str(workdir))
        if db.account != account:
            raise RuntimeError("Reader selected a different account")
        required = [rel for rel, path, _ in db._db_files if os.path.basename(path) == "contact.db"
                    or re.fullmatch(r"message_\d+\.db", os.path.basename(path))]
        if not required or any(rel in db.unkeyed for rel in required):
            raise RuntimeError("Required contact or message database has no verified key")
        key_files = [Path(db.keys_file), Path(db._stable_key_file())]
        if any(p.exists() and not p.read_bytes().startswith(b"dpapi1:") for p in key_files):
            raise RuntimeError("Key cache is not DPAPI encrypted")
        with silent_upstream():
            contact_rel = next(rel for rel, path, _ in db._db_files if os.path.basename(path) == "contact.db")
            contact = db._open(contact_rel)
            try:
                flags = {u: int(v or 0) for u, v in contact.execute("SELECT username,verify_flag FROM contact")}
            finally:
                contact.close()
            nicks = db.nickname_map()
            chats = db.list_message_chats()
            self_wxid = db.wxid
            for rel in db._message_dbs():
                c = db._open(rel)
                conns.append((c, db._shard_label(c), db._name2id_map(c)))
        archive = Archive(account)
        for chat in chats:
            user = chat["username"]
            if not allowed_chat(user, flags.get(user)):
                stats["skipped"] += 1
                continue
            archive.ensure_chat(user, chat["name"])
            table = "Msg_" + hashlib.md5(user.encode("utf-8")).hexdigest()
            for conn, shard, sender_ids in conns:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                cursor = conn.execute(
                    f"SELECT local_id,local_type,real_sender_id,create_time,message_content,"
                    f"compress_content,server_id,sort_seq FROM {table}"
                )
                while True:
                    batch = cursor.fetchmany(1000)
                    if not batch:
                        break
                    normalized = []
                    for row in batch:
                        body = message_body(row[1], row[4], row[5])
                        if body is None:
                            continue
                        try:
                            sender_user = sender_ids.get(int(row[2]), "")
                        except (ValueError, TypeError):
                            sender_user = ""
                        sender = "我" if sender_user == self_wxid else nicks.get(sender_user, sender_user)
                        if not sender:
                            sender = "未知发送者"
                        normalized.append((
                            msg_identity(row[6], shard, row[0]), timestamp(row[3]),
                            int(row[7] or 0), sender, body,
                        ))
                    stats["new_messages"] += archive.add_batch(user, normalized)
            stats["chats"] += 1
        stats["files"] = archive.render_dirty()
        return stats
    finally:
        for conn, _, _ in conns:
            conn.close()
        if archive is not None:
            archive.close()
        _cleanup_temp(workdir)


def run_once() -> int:
    if not version_supported():
        log("unsupported_weixin_version")
        return 1
    if refresh_data_root() is None:
        log("no_data_root")
        return 1
    accounts = live_accounts()
    if not accounts:
        log("no_live_account")
        return 1
    failures = 0
    for account in accounts:
        try:
            stats = export_account(account)
            log("export_ok", account=account, **stats)
        except Exception as exc:
            failures += 1
            log("export_failed", account=account, error=type(exc).__name__)
    return 1 if failures else 0


def write_status() -> None:
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    target = LOCAL_ROOT / "status.json"
    pending = target.with_suffix(".pending")
    pending.write_text(
        json.dumps({"version": APP_VERSION, "started_unix": int(time.time()), "pid": os.getpid()}),
        encoding="utf-8",
    )
    os.replace(pending, target)


def maybe_update() -> bool:
    """Only installed binaries update; source development runs never self-update."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        import updates

        latest = updates.latest_release(APP_VERSION)
        if latest is None:
            return False
        manifest, url = latest
        new_dir = updates.stage_release(manifest, url, LOCAL_ROOT)
        updates.launch_switch(new_dir, APP_VERSION, LOCAL_ROOT)
        log("update_staged", version=manifest["version"])
        return True
    except Exception as exc:
        log("update_check_failed", error=type(exc).__name__)
        return False


class LoginTracker:
    """One export per observed account login, including same-process relogin."""

    def __init__(self):
        self.active: dict[str, tuple[int, int]] = {}
        self.completed: set[tuple[str, tuple[int, int]]] = set()

    def observe(self, live: set[str], markers: dict[str, tuple[int, int]]) -> list[str]:
        for account in set(self.active) - live:
            self.active.pop(account, None)
        due = []
        for account in sorted(live):
            token = markers.get(account, (0, 0))
            if self.active.get(account) != token:
                self.active[account] = token
                self.completed.discard((account, token))
            if (account, token) not in self.completed:
                due.append(account)
        return due

    def mark_done(self, account: str) -> None:
        self.completed.add((account, self.active[account]))


def watch() -> None:
    write_status()
    log("watch_started")
    last_signature = ()
    last_markers = {}
    last_probe = 0.0
    tracker = LoginTracker()
    retry: dict[str, float] = {}
    failures: dict[str, int] = {}
    blocked: set[tuple[str, tuple[int, int]]] = set()
    update_checked: set[tuple[str, tuple[int, int]]] = set()
    current_root = None
    while True:
        try:
            root = refresh_data_root()
            if root != current_root:
                current_root = root
                last_markers = {}
                tracker = LoginTracker()
                retry.clear()
                failures.clear()
                blocked.clear()
                update_checked.clear()
            signature = process_signature()
            markers = {p.name: login_marker(p.name) for p in account_dirs()}
            now = time.monotonic()
            changed = signature != last_signature or markers != last_markers
            due_retry = any(t <= now for t in retry.values())
            if changed or due_retry or now - last_probe >= HEARTBEAT_SECONDS:
                last_signature, last_markers, last_probe = signature, markers, now
                live = set(live_accounts()) if signature else set()
                for account in set(tracker.active) - live:
                    retry.pop(account, None)
                    failures.pop(account, None)
                    update_checked = {item for item in update_checked if item[0] != account}
                for account in tracker.observe(live, markers):
                    token = tracker.active[account]
                    if (account, token) in blocked:
                        continue
                    if retry.get(account, 0) > now:
                        continue
                    should_check_update = (account, token) not in update_checked
                    if should_check_update:
                        update_checked.add((account, token))
                    if not version_supported():
                        log("unsupported_weixin_version")
                        blocked.add((account, token))
                        if should_check_update and maybe_update():
                            return
                        continue
                    try:
                        stats = export_account(account)
                        tracker.mark_done(account)
                        retry.pop(account, None)
                        failures.pop(account, None)
                        log("export_ok", account=account, **stats)
                        if should_check_update and maybe_update():
                            return
                    except Exception as exc:
                        failures[account] = failures.get(account, 0) + 1
                        log("export_failed", account=account, error=type(exc).__name__)
                        if should_check_update and maybe_update():
                            return
                        if failures[account] >= 3:
                            blocked.add((account, token))
                            log("export_disabled", account=account)
                        else:
                            retry[account] = now + RETRY_SECONDS
        except Exception as exc:
            log("watch_error", error=type(exc).__name__)
        time.sleep(POLL_SECONDS)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "version":
        print(APP_VERSION)
        return 0
    if mode == "preflight":
        return 0 if version_supported() else 3
    if mode == "root-status":
        return 0 if refresh_data_root() is not None else 4
    if mode == "repair":
        return repair_data_root()
    if mode == "once":
        return run_once()
    if mode == "watch":
        watch()
        return 0
    print("Usage: wechat_text_export.py [once|watch]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
