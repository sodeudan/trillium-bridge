import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import import_transcript as importer


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_vtt_speaker_and_entities(self):
        source = self.root / "meeting.vtt"
        source.write_text(
            "\ufeffWEBVTT\n\nNOTE metadata\nignore me\n\n"
            "cue1\n00:00:01.000 --> 00:00:03.000\n"
            "<v Alice>Decided: A &amp; B.</v>\n\n"
            "2\n00:00:03.000 --> 00:00:05.000\n<v Bob>I will follow up.</v>\n",
            encoding="utf-8",
        )
        self.assertEqual(
            importer.read_transcript(source),
            "Alice: Decided: A & B.\nBob: I will follow up.",
        )

    def test_docx_paragraphs(self):
        source = self.root / "meeting.docx"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main"><w:body><w:p><w:r>'
                '<w:t>Alice: Hello</w:t></w:r></w:p><w:p><w:r>'
                '<w:t>Bob: Hi</w:t></w:r></w:p></w:body></w:document>',
            )
        self.assertEqual(importer.read_transcript(source), "Alice: Hello\nBob: Hi")

    def test_empty_and_oversize_rejected(self):
        source = self.root / "meeting.txt"
        source.write_text(" ")
        with self.assertRaises(importer.ImportFailure):
            importer.read_transcript(source)
        source.write_text("a" * (importer.MAX_CHARACTERS + 1))
        with self.assertRaisesRegex(importer.ImportFailure, "split"):
            importer.read_transcript(source)

    def test_private_service_urls_only(self):
        self.assertEqual(importer.private_url("http://127.0.0.1:8000/"), "http://127.0.0.1:8000")
        for url in ("https://example.org", "http://localhost@example.org", "http://127.0.0.1:8000/path"):
            with self.subTest(url=url), self.assertRaises(argparse.ArgumentTypeError):
                importer.private_url(url)

    def test_chunking_keeps_all_source(self):
        source = "x" * (importer.CHUNK_SIZE * 4 + 7)
        with patch.object(importer, "generate", return_value="Summary") as generate:
            self.assertEqual(importer.summarize(source, "local", "model"), "Summary")
        chunks = [call.args[0] for call in generate.call_args_list[:5]]
        self.assertEqual("".join(chunks), source)
        self.assertEqual(generate.call_count, 8)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []
        self.fail_write = False
        self.fail_model = False
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                test.requests.append((self.path, payload))
                if self.path == "/api/chat":
                    result = {"message": {"content": "Summary\nDecision: launch."}}
                    status = 404 if test.fail_model else 200
                else:
                    result = {"noteId": "test-note", "action": "created"}
                    status = 502 if test.fail_write else 200
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(self.server.shutdown)
        url = f"http://127.0.0.1:{self.server.server_port}"
        self.args = argparse.Namespace(
            bridge_url=url, ollama_url=url, model="qwen3:8b",
            parent=["Meetings", "Summaries"], state=self.root / "ledger.sqlite3",
            dry_run=False, include_transcript=False, settle_seconds=10,
        )
        self.source = self.root / "meeting.txt"
        self.source.write_text("Alice: We decided to launch.", encoding="utf-8")

    def test_import_and_duplicate_after_filename_change(self):
        with self.assertLogs(importer.LOGGER, level="INFO") as logs:
            importer.import_file(self.source, self.args)
        messages = "\n".join(logs.output)
        self.assertIn("Summarizing meeting.txt", messages)
        self.assertIn("Summary ready for meeting.txt", messages)
        self.assertIn("Writing meeting.txt to Trilium", messages)
        self.assertNotIn("Alice:", messages)
        renamed = self.root / "renamed.txt"
        self.source.rename(renamed)
        importer.import_file(renamed, self.args)
        self.assertEqual(len(self.requests), 2)
        payload = self.requests[1][1]
        self.assertEqual(payload["path"][:2], self.args.parent)
        self.assertIn("Decision: launch.", payload["content"])
        self.assertNotIn("Original transcript", payload["content"])
        self.assertFalse(self.requests[0][1]["think"])
        with importer.open_ledger(self.args.state) as ledger:
            self.assertEqual(ledger.execute("SELECT status FROM imports").fetchone()[0], "done")

    def test_uncertain_write_not_retried(self):
        self.fail_write = True
        with self.assertRaises(importer.ImportFailure):
            importer.import_file(self.source, self.args)
        with self.assertRaisesRegex(importer.ImportFailure, "uncertain outcome"):
            importer.import_file(self.source, self.args)
        self.assertEqual(len(self.requests), 2)

    def test_model_failure_can_retry(self):
        self.fail_model = True
        with self.assertRaises(importer.ImportFailure):
            importer.import_file(self.source, self.args)
        self.fail_model = False
        importer.import_file(self.source, self.args)
        self.assertEqual(len(self.requests), 3)

    def test_dry_run_and_optional_source(self):
        self.args.dry_run = True
        self.args.include_transcript = True
        with patch("builtins.print") as output:
            importer.import_file(self.source, self.args)
        preview = json.loads(output.call_args.args[0])
        self.assertIn("Original transcript", preview["content"])
        self.assertEqual(len(self.requests), 1)
        with importer.open_ledger(self.args.state) as ledger:
            self.assertEqual(ledger.execute("SELECT count(*) FROM imports").fetchone()[0], 0)

    def test_watching_skips_recent_and_unsupported_files(self):
        (self.root / "ignore.pdf").write_text("not a transcript")
        with patch.object(importer, "import_file") as process:
            self.assertFalse(importer.scan_folder(self.root, self.args))
            process.assert_not_called()
        self.args.settle_seconds = 0
        importer.scan_folder(self.root, self.args)
        self.assertEqual(len(self.requests), 2)


if __name__ == "__main__":
    unittest.main()
