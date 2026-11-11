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

    def test_login_tracker_periodic_relogin_and_resume(self):
        tracker = app.LoginTracker()
        markers = {"a": (1, 1), "b": (2, 2)}
        signature = ((42, 1.0),)
        self.assertEqual(tracker.observe({"a", "b"}, markers, signature, 10), ["a", "b"])
        tracker.mark_attempt("a", 10)
        tracker.mark_attempt("b", 12)
        self.assertFalse(tracker.has_due(309))
        self.assertEqual(tracker.observe({"a", "b"}, markers, signature, 310), ["a"])
        self.assertEqual(tracker.observe({"a", "b"}, markers, signature, 312), ["a", "b"])
        tracker.mark_attempt("a", 312)
        tracker.mark_attempt("b", 312)
        self.assertEqual(tracker.observe({"a", "b"}, {"a": (3, 3), "b": (2, 2)}, signature, 313), ["a"])
        tracker.mark_attempt("a", 313)
        tracker.force_due()
        self.assertEqual(tracker.observe({"a", "b"}, {"a": (3, 3), "b": (2, 2)}, signature, 314), ["a", "b"])
        self.assertEqual(tracker.observe(set(), markers, (), 315), [])
        self.assertEqual(tracker.observe({"a"}, {"a": (3, 3)}, ((43, 2.0),), 316), ["a"])

    def test_watch_periodic_scans_failure_recovery_and_no_success_log(self):
        clock = type("Clock", (), {"now": 10.0})()
        errors = app.ErrorReporter()
        state = app.WatchState(errors)
        attempts = []

        def export(_account):
            attempts.append(clock.now)
            if len(attempts) in (1, 2, 4):
                raise ValueError("simulated read failure")

        with patch.object(app, "refresh_data_root", return_value=Path("data")), \
                patch.object(app, "process_signature", return_value=((42, 1.0),)), \
                patch.object(app, "account_dirs", return_value=[Path("a")]), \
                patch.object(app, "login_marker", return_value=(1, 1)), \
                patch.object(app, "live_accounts", return_value=["a"]), \
                patch.object(app, "version_supported", return_value=True), \
                patch.object(app, "export_account", side_effect=export), \
                patch.object(app, "maybe_update", return_value=False), \
                patch.object(app.time, "monotonic", side_effect=lambda: clock.now), \
                patch.object(app, "log") as log:
            for moment in (10, 309, 310, 610, 910):
                clock.now = moment
                self.assertFalse(state.step(clock.now))
            self.assertEqual(attempts, [10, 310, 610, 910])
            self.assertEqual(log.call_count, 2)
            self.assertEqual([call.args[0] for call in log.call_args_list],
                             ["export_failed", "export_failed"])

    def test_watch_resume_and_multiple_accounts_never_overlap(self):
        clock = type("Clock", (), {"now": 10.0})()
        state = app.WatchState(app.ErrorReporter())
        active = 0
        visits = []

        def export(account):
            nonlocal active
            active += 1
            self.assertEqual(active, 1)
            visits.append((account, clock.now))
            active -= 1

        with patch.object(app, "refresh_data_root", return_value=Path("data")), \
                patch.object(app, "process_signature", return_value=((42, 1.0),)), \
                patch.object(app, "account_dirs", return_value=[Path("a"), Path("b")]), \
                patch.object(app, "login_marker", return_value=(1, 1)), \
                patch.object(app, "live_accounts", return_value=["a", "b"]), \
                patch.object(app, "version_supported", return_value=True), \
                patch.object(app, "export_account", side_effect=export), \
                patch.object(app, "maybe_update", return_value=False), \
                patch.object(app.time, "monotonic", side_effect=lambda: clock.now), \
                patch.object(app, "log") as log:
            state.step(clock.now)
            clock.now = 100
            state.step(clock.now, resumed=True)
            self.assertEqual(visits, [("a", 10), ("b", 10), ("a", 100), ("b", 100)])
            log.assert_not_called()

    def test_overrun_skips_overlap_and_rechecks_after_completion(self):
        clock = type("Clock", (), {"now": 10.0})()
        state = app.WatchState(app.ErrorReporter())
        calls = []

        def slow_export(account):
            calls.append((account, clock.now))
            if len(calls) == 1:
                clock.now = 400  # The first full scan exceeded the five-minute interval.

        with patch.object(app, "refresh_data_root", return_value=Path("data")), \
                patch.object(app, "process_signature", return_value=((42, 1.0),)), \
                patch.object(app, "account_dirs", return_value=[Path("a")]), \
                patch.object(app, "login_marker", return_value=(1, 1)), \
                patch.object(app, "live_accounts", return_value=["a"]), \
                patch.object(app, "version_supported", return_value=True), \
                patch.object(app, "export_account", side_effect=slow_export), \
                patch.object(app, "maybe_update", return_value=False), \
                patch.object(app.time, "monotonic", side_effect=lambda: clock.now):
            state.step(clock.now)
            state.step(clock.now)
        self.assertEqual(calls, [("a", 10), ("a", 400)])

    def test_unsupported_version_never_invokes_export(self):
        clock = type("Clock", (), {"now": 10.0})()
        state = app.WatchState(app.ErrorReporter())
        with patch.object(app, "refresh_data_root", return_value=Path("data")), \
                patch.object(app, "process_signature", return_value=((42, 1.0),)), \
                patch.object(app, "account_dirs", return_value=[Path("a")]), \
                patch.object(app, "login_marker", return_value=(1, 1)), \
                patch.object(app, "live_accounts", return_value=["a"]), \
                patch.object(app, "version_supported", return_value=False), \
                patch.object(app, "export_account") as export, \
                patch.object(app, "maybe_update", return_value=False), \
                patch.object(app.time, "monotonic", side_effect=lambda: clock.now), \
                patch.object(app, "log") as log:
            state.step(clock.now)
            clock.now = 310
            state.step(clock.now)
            export.assert_not_called()
            self.assertEqual(log.call_count, 1)
            self.assertEqual(log.call_args.args[0], "unsupported_weixin_version")

    def test_stale_snapshot_cleanup_is_limited_to_app_temp(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app, "LOCAL_ROOT", Path(tmp)):
            stale = Path(tmp) / "tmp" / "account_a"
            stale.mkdir(parents=True)
            (stale / "message_0.db").write_bytes(b"temporary decrypted snapshot")
            app.cleanup_stale_temp()
            self.assertFalse(stale.exists())
            nested = Path(tmp) / "tmp" / "account_b" / "unexpected"
            nested.mkdir(parents=True)
            with self.assertRaises(RuntimeError):
                app.cleanup_stale_temp()
            self.assertTrue(nested.exists())


if __name__ == "__main__":
    unittest.main()
