# Local meeting summaries

Download a Teams transcript as `.vtt` or `.docx`, or supply a UTF-8 `.txt` transcript.
This standalone importer sends it to **local Ollama** and writes the summary through
the existing Trilium bridge. It does not join meetings, record audio, or require
Graph permissions, Azure hosting, or a public tunnel. The Copilot agent is unchanged.

## Setup

1. Install [Ollama](https://ollama.com/download) for your operating system and start
   its local service. On Linux, `ollama serve` runs in the foreground; keep it in a
   separate terminal or use Ollama's service.
2. Download the selected model: `ollama pull qwen3:8b`. This is a multi-GB download;
   check available disk and RAM first. You can select a different installed model
   with `--model`.
3. Keep the Trilium bridge running at `http://127.0.0.1:8000`.
4. Use Python 3.10 or newer. The importer needs only the standard library, with no
   additional packages. Run commands below from the repository root.

Only loopback service URLs are accepted. Transcripts stay on your computer unless
your local model/service configuration forwards them elsewhere. Use only approved
transcripts and follow your organization's consent and retention policies.

## Preview, import, and watch

Preview a summary without writing a Trilium note:

```bash
.venv/bin/python meeting-summary/import_transcript.py \
  "/absolute/path/to/meeting.vtt" --dry-run
```

Import into `Meetings / Summaries`, or choose an explicit parent hierarchy:

```bash
.venv/bin/python meeting-summary/import_transcript.py \
  "/absolute/path/to/meeting.docx" --parent Meetings Summaries
```

Import all completed downloads in a folder once, then exit:

```bash
.venv/bin/python meeting-summary/import_transcript.py \
  "$HOME/meeting-transcripts"
```

Already imported transcripts are skipped. Folder scans skip files modified in the
last 10 seconds; wait until downloads finish before running. A single-file import
runs immediately, so pass only a fully saved file.

Optional: watch an existing folder for downloaded transcripts:

```bash
.venv/bin/python meeting-summary/import_transcript.py \
  "/absolute/path/to/transcripts" --watch
```

Watch mode stays running until stopped. To run unattended, configure it as a
user service with the **absolute Python, script, and folder paths**, and enable
that service at login. It does not run automatically just because files exist.
By default the scan interval is 30 seconds and files must be unchanged for at
least 10 seconds. This is a save-completion heuristic; for slow downloads, stage
files elsewhere and move them into the watched folder only once complete.
Subfolders are not scanned. Original files are never moved or deleted.
These intervals control detection, not completion: CPU-only summarization with
`qwen3:8b` can take several minutes before a note appears. Watch the service log
for `Summarizing`, `Summary ready`, `Writing`, and `Imported` progress messages.
An empty file is rejected for that scan but retried after its contents are saved.

### Configured setup on this computer

Ollama is installed under `~/.local/opt/trilium-ollama` with cloud features
disabled and its listener restricted to `127.0.0.1:11434`. The chosen model is
`qwen3:8b`. Transcripts can be kept in `~/meeting-transcripts`.
**Imports are manual:** `trilium-meeting-summary.service` is stopped and disabled,
so the folder is not checked periodically or at login. Ollama's user service,
`trilium-ollama.service`, remains enabled at login.

```bash
~/copilot-trilium/meeting-summary-agent.sh
```

This command works from any directory and exits after processing the folder.
Progress is printed in the terminal. Additional importer options can be passed
to the script, for example `~/copilot-trilium/meeting-summary-agent.sh --dry-run`.
To import just one transcript, invoke `import_transcript.py` directly as shown
above.

The retained unit files are in `~/.config/systemd/user/` and use this computer's
absolute paths. Keep the bridge running separately. Login startup does not imply
running while logged out; user lingering is not enabled by this setup.

Summaries contain discussion points, decisions, action items, and open questions.
Owners and deadlines must be stated in the transcript; generated summaries still
need human review. The note title uses the filename and a content fingerprint,
not an invented meeting date. Add `--include-transcript` to store the source text
alongside the summary. By default only the summary and import metadata are saved.
Long transcripts are summarized in chunks, then consolidated. Files over 5 MB,
DOCX document XML over 5 MB, and text over 120,000 characters are rejected explicitly
instead of silently truncated.

## Duplicate prevention and failures

The bridge's [`/write-note`](../main.py) endpoint **appends** to an existing note.
The importer therefore keeps a SQLite ledger in
`~/.local/state/trilium-meeting-summary/imports.sqlite3`.
The same parsed transcript and destination are imported only once, even after
restart or a filename change. Changed text is treated as a new import.
Preserve this ledger; deleting it removes duplicate protection. Override its
location with `--state`.

If a write fails or its response is lost, the ledger retains a `pending` entry.
The importer **does not retry that write automatically** because Trilium might
already have created the note. Inspect the note and ledger before reconciling it:

- If the note exists, mark the matching fingerprint `done` and set its `note_id`.
- If you confirm nothing was written, delete only that fingerprint's pending
  entry and rerun the import.

Ollama connection/model failures occur before reserving the write, so they can
be retried after fixing the service. Watch mode logs failures and continues
checking other files; a single import or one-shot folder scan exits nonzero on
failure. Logs do not include transcript contents. `--dry-run` intentionally prints
the summary, and the source too if `--include-transcript` is selected.

## Tests

```bash
.venv/bin/python -m unittest discover -s meeting-summary -p 'test_*.py' -v
```

Tests use a local mock HTTP server and temporary files. They never access Ollama
or create actual Trilium notes.
