from __future__ import annotations

import json
from contextlib import contextmanager
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from .contracts import Session, ToolError


def utc_now():
    return datetime.now(timezone.utc).isoformat()


# --- CRM boundary: accept only the context this workflow understands ---
def validate_crm(value, client_id):
    if not isinstance(value, dict) or value.get('client_id') != client_id:
        raise ToolError('CRM response has an invalid client identity')
    if value.get('risk_tolerance') not in ('conservative', 'balanced', 'growth'):
        raise ToolError('CRM response has an invalid risk profile')
    # Whitelist fields: arbitrary remote instructions never become orchestration instructions.
    return {'client_id': client_id, 'risk_tolerance': value['risk_tolerance'], 'source': 'crm'}


class MockCRMTool:
    def __init__(self, fail=False):
        self.fail = fail

    def fetch(self, client_id):
        if self.fail:
            raise ToolError('Simulated CRM outage')
        return validate_crm({'client_id': client_id, 'risk_tolerance': 'balanced'}, client_id)


class HttpCRMTool:
    def __init__(self, base_url, timeout=2.0, attempts=3):
        parsed = urlparse(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('CRM URL must be HTTP(S) without embedded credentials')
        if not 1 <= attempts <= 5 or not 0 < timeout <= 30:
            raise ValueError('Invalid CRM retry or timeout budget')
        self.base_url, self.timeout, self.attempts = base_url.rstrip('/'), timeout, attempts
        self.last_attempts = 0

    def fetch(self, client_id):
        for attempt in range(self.attempts):
            self.last_attempts = attempt + 1
            try:
                with urlopen(f'{self.base_url}/clients/{quote(client_id, safe="")}', timeout=self.timeout) as response:
                    body = response.read(65537)
                    if len(body) > 65536:
                        raise ToolError('CRM response exceeds size limit')
                    return validate_crm(json.loads(body), client_id)
            except HTTPError as exc:
                # Retrying a missing client or a bad request will not repair it.
                if exc.code not in (429, 500, 502, 503, 504):
                    raise ToolError(f'CRM HTTP {exc.code}; no retry') from exc
            except (URLError, TimeoutError, OSError):
                pass
            except (ValueError, UnicodeError) as exc:
                raise ToolError('CRM returned malformed JSON') from exc
            if attempt + 1 < self.attempts:
                time.sleep(.05 * (2 ** attempt))
        raise ToolError(f'CRM unavailable after {self.attempts} attempts')


# --- Shared data foundation and long-term memory ---
class SQLiteMemory:
    """One local foundation: all zones and durable summaries in one transaction."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, client_id TEXT, created_at TEXT,
                    raw TEXT, cleaned TEXT, curated TEXT, result TEXT);
                CREATE INDEX IF NOT EXISTS runs_client ON runs(client_id, created_at);
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY, run_id TEXT, created_at TEXT,
                    reviewer TEXT, decision TEXT, note TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id));
            ''')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            with conn:
                yield conn
        finally:
            # SQLite transaction contexts do not close the connection themselves.
            # Explicit cleanup also avoids locked database files on Windows.
            conn.close()

    # History follows execution order, even when a user reruns a backdated snapshot.
    def previous(self, client_id):
        with self.connect() as conn:
            row = conn.execute('SELECT result FROM runs WHERE client_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
                               (client_id,)).fetchone()
        if not row:
            return None
        result = json.loads(row[0])
        return {'run_id': result['run_id'], 'risk_level': result['risk_level'],
                'signal_codes': sorted({s['code'] for s in result['analysis']['signals']})}

    def save(self, session: Session, result):
        encode = lambda value: json.dumps(value, allow_nan=False)
        with self.connect() as conn:
            conn.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)',
                         (session.run_id, session.cleaned['client_id'], utc_now(),
                          encode(session.raw), encode(session.cleaned), encode(session.curated), encode(result)))

    def get(self, run_id):
        with self.connect() as conn:
            row = conn.execute('SELECT result FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown run_id')
        return json.loads(row[0])

    # --- Human checkpoint: record the disposition without rewriting the analysis ---
    def review(self, run_id, reviewer, decision, note):
        if decision not in ('approve', 'reject', 'override'):
            raise ValueError('Decision must be approve, reject, or override')
        if not reviewer.strip() or not note.strip():
            raise ValueError('Reviewer and rationale are required')
        with self.connect() as conn:
            # Take the write lock before reading status to prevent two final decisions.
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT result FROM runs WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown run_id')
            result = json.loads(row[0])
            if result['status'] != 'pending_review':
                raise ValueError('Run has already been reviewed')
            result['status'] = {'approve': 'approved', 'reject': 'rejected', 'override': 'overridden'}[decision]
            result['review'] = {'reviewer': reviewer, 'decision': decision, 'note': note, 'at': utc_now()}
            # Overrides are a separate advisor disposition; analytical evidence remains unchanged.
            conn.execute('INSERT INTO reviews(run_id,created_at,reviewer,decision,note) VALUES (?,?,?,?,?)',
                         (run_id, result['review']['at'], reviewer, decision, note))
            conn.execute('UPDATE runs SET result=? WHERE run_id=?', (json.dumps(result), run_id))
        return result
