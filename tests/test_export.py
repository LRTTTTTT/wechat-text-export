import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wechat_text_export as app


class ArchiveTests(unittest.TestCase):
    def test_backfill_deduplicate_delete_and_late_arrival(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "OUTPUT_ROOT", Path(tmp)):
            archive = app.Archive("account_a")
            archive.ensure_chat("friend", "甲/乙")
            self.assertEqual(archive.add_batch("friend", [
                ("s:2", 200, 2, "甲", "后来的消息"),
                ("s:2", 200, 2, "甲", "重复消息"),
            ]), 1)
            self.assertEqual(archive.render_dirty(), 1)
            self.assertEqual(archive.add_batch("friend", [
                ("s:1", 100, 1, "我", "较早的消息"),
            ]), 1)
            archive.render_dirty()
            name = archive.conn.execute("SELECT filename FROM chats").fetchone()[0]
            content = (Path(tmp) / "account_a" / name).read_text(encoding="utf-8")
            self.assertLess(content.index("较早的消息"), content.index("后来的消息"))
            self.assertNotIn("重复消息", content)
            archive.close()

            # The source no longer provides s:2; the indexed export preserves it.
            reopened = app.Archive("account_a")
            reopened.ensure_chat("friend", "新的昵称")
            self.assertEqual(reopened.render_dirty(), 0)
            self.assertEqual(reopened.conn.execute("SELECT count(*) FROM messages").fetchone()[0], 2)
            self.assertEqual(reopened.conn.execute("SELECT filename FROM chats").fetchone()[0], name)
            reopened.close()

    def test_accounts_are_separate(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "OUTPUT_ROOT", Path(tmp)):
            for account, body in (("a", "第一账号"), ("b", "第二账号")):
                archive = app.Archive(account)
                archive.ensure_chat("same_chat", "同名")
                archive.add_batch("same_chat", [("s:1", 1, 1, "我", body)])
                archive.render_dirty()
                archive.close()
            a_text = next((Path(tmp) / "a").glob("*.txt")).read_text(encoding="utf-8")
            b_text = next((Path(tmp) / "b").glob("*.txt")).read_text(encoding="utf-8")
            self.assertIn("第一账号", a_text)
            self.assertNotIn("第二账号", a_text)
            self.assertIn("第二账号", b_text)

    def test_text_media_scope_and_identity(self):
        self.assertEqual(app.message_body(1, "第一行\r\n第二行"), "第一行\n第二行")
        self.assertEqual(app.message_body(3, b"ignored"), "[图片]")
        self.assertEqual(app.message_body(34, b"ignored"), "[语音]")
        self.assertIsNone(app.message_body(10000, "system"))
        self.assertEqual(app.msg_identity(123, "a", 5), "s:123")
        self.assertEqual(app.msg_identity(0, "a", 5), "l:a:5")
        self.assertFalse(app.allowed_chat("gh_test", 24))
        self.assertFalse(app.allowed_chat("friend", 8))
        self.assertTrue(app.allowed_chat("filehelper", 0))
        self.assertTrue(app.allowed_chat("group@chatroom", 0))

    def test_login_tracker_relogin_same_process_and_multiple_accounts(self):
        tracker = app.LoginTracker()
        markers = {"a": (1, 1), "b": (2, 2)}
        self.assertEqual(tracker.observe({"a", "b"}, markers), ["a", "b"])
        tracker.mark_done("a")
        tracker.mark_done("b")
        self.assertEqual(tracker.observe({"a", "b"}, markers), [])
        self.assertEqual(tracker.observe({"a", "b"}, {"a": (3, 3), "b": (2, 2)}), ["a"])
        tracker.mark_done("a")
        self.assertEqual(tracker.observe(set(), markers), [])
        self.assertEqual(tracker.observe({"a"}, {"a": (3, 3)}), ["a"])


if __name__ == "__main__":
    unittest.main()
