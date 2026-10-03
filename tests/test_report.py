import json
import tempfile
import unittest
from pathlib import Path

from social_topics.__main__ import main
from social_topics.models import Post

POSTS = [
    Post("twitter", "1", "https://x.com/a/status/1", "Interest rates are rising #economy", "@a", likes=10),
    Post("youtube", "2", "https://youtu.be/2", "Interest rates explained #economy", "Ch", views=9000),
    Post("facebook", "3", "https://fb.com/3", "New interest rates announced", "Bank", shares=4),
]


class CliReportTests(unittest.TestCase):
    def test_scan_from_file_writes_report_and_index(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "posts.json"
            src.write_text(json.dumps([p.to_dict() for p in POSTS]))
            out = Path(d) / "data"

            self.assertEqual(main(["scan", "--from-file", str(src), "--out", str(out), "--report-id", "r1"]), 0)
            report = json.loads((out / "reports" / "r1.json").read_text())
            self.assertEqual(report["totals"]["posts"], 3)
            self.assertEqual(report["topics"][0]["label"], "interest rates")
            self.assertFalse(report["topics"][0]["new"])  # nothing to compare with yet
            self.assertEqual(report["hashtags"], [{"tag": "#economy", "count": 2}])

            main(["scan", "--from-file", str(src), "--out", str(out), "--report-id", "r2"])
            index = json.loads((out / "index.json").read_text())
            self.assertEqual([r["id"] for r in index["reports"]], ["r2", "r1"])
            self.assertEqual(index["reports"][0]["top_topics"][0], "interest rates")

    def test_new_topics_are_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "data"
            first, second = Path(d) / "a.json", Path(d) / "b.json"
            first.write_text(json.dumps([p.to_dict() for p in POSTS]))
            extra = POSTS + [
                Post("twitter", "4", "u", "Solar eclipse tonight", "@b"),
                Post("instagram", "5", "u", "Solar eclipse photos", "@c"),
            ]
            second.write_text(json.dumps([p.to_dict() for p in extra]))
            main(["scan", "--from-file", str(first), "--out", str(out), "--report-id", "a"])
            main(["scan", "--from-file", str(second), "--out", str(out), "--report-id", "b"])
            topics = {t["label"]: t["new"] for t in json.loads((out / "reports" / "b.json").read_text())["topics"]}
            self.assertTrue(topics["solar eclipse"])
            self.assertFalse(topics["interest rates"])

    def test_fails_when_no_platform_returns_data(self):
        with tempfile.TemporaryDirectory() as d:
            code = main(["scan", "--platforms", "twitter", "--out", d, "--config", "/nonexistent.json"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
