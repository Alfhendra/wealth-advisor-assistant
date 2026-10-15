from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from .contracts import AnalysisTool, CRMTool, DataTool, InputError, MemoryTool, Session, ToolError

LOG = logging.getLogger('wealth_advisor')


def event(session, step, **details):
    session.trace.append(step)
    # Deliberately omit raw client data, amounts and third-party exception text.
    LOG.info(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(), 'run_id': session.run_id, 'event': step, **details}))


# --- Specialist agents ---
# Each stage reads and updates the shared session.
class DataFetcherAgent:
    def __init__(self, data: DataTool, crm: CRMTool):
        self.data, self.crm = data, crm

    def execute(self, session: Session):
        session.cleaned = self.data.prepare(session.raw)
        event(session, 'data.validated', duplicates_removed=session.cleaned['quality']['duplicates_removed'])
        try:
            context = self.crm.fetch(session.cleaned['client_id'])
            event(session, 'crm.fetched')
        except ToolError:
            # An outage must not silently turn into an assumed client risk preference.
            context = {'client_id': session.cleaned['client_id'], 'risk_tolerance': 'unknown', 'source': 'fallback'}
            session.warnings.append('CRM unavailable or invalid. Client suitability is unknown; advisor must verify.')
            event(session, 'crm.fallback', error_type='ToolError')
        session.curated = {**session.cleaned, 'crm': context}


class AnalyzerAgent:
    def __init__(self, analysis: AnalysisTool):
        self.analysis = analysis

    def execute(self, session: Session):
        result = self.analysis.analyze(session.curated)
        event(session, 'analysis.completed', signals=len(result['signals']))
        return result


class AdvisoryAgent:
    def execute(self, session: Session, analysis):
        severities = {s['severity'] for s in analysis['signals']}
        risk = 'high' if 'high' in severities else 'medium' if severities else 'low'
        # Compare signal codes, not wording, so recurring findings have stable identities.
        previous = set(session.history['signal_codes']) if session.history else set()
        recurring = sorted(previous & {s['code'] for s in analysis['signals']})
        event(session, 'advisory.review_required', risk_level=risk)
        return {'schema_version': '1.0', 'run_id': session.run_id,
                'client_id': session.cleaned['client_id'], 'as_of': session.cleaned['as_of'],
                'currency': session.cleaned['currency'], 'status': 'pending_review', 'risk_level': risk,
                'crm_context': session.curated['crm'], 'data_quality': session.cleaned['quality'],
                'analysis': analysis, 'previous_summary': session.history, 'recurring_signal_codes': recurring,
                'warnings': session.warnings, 'trace': session.trace,
                'review': None, 'notice': 'Synthetic demonstration. Advisor review required; no trades or client messages are executed.'}


# --- Central execution flow ---
class Orchestrator:
    def __init__(self, fetcher: DataFetcherAgent, analyzer: AnalyzerAgent,
                 advisor: AdvisoryAgent, memory: MemoryTool):
        self.fetcher, self.analyzer, self.advisor, self.memory = fetcher, analyzer, advisor, memory

    def run(self, raw):
        session = Session(str(uuid.uuid4()), raw)
        event(session, 'orchestrator.started')
        try:
            # 1. Validate and enrich before allowing any financial analysis.
            self.fetcher.execute(session)

            # 2. History adds context; its absence should not block current-session analysis.
            try:
                session.history = self.memory.previous(session.cleaned['client_id'])
                event(session, 'memory.loaded', history_found=session.history is not None)
            except (OSError, sqlite3.Error, ValueError):
                session.warnings.append('Historical memory unavailable; analysis uses this session only.')
                event(session, 'memory.read_failed')

            # 3. Calculate findings, then prepare a decision for the human reviewer.
            analysis = self.analyzer.execute(session)
            result = self.advisor.execute(session, analysis)
            event(session, 'orchestrator.completed')

            # 4. A reviewable result must have a durable record of its supporting data.
            try:
                self.memory.save(session, result)
            except (OSError, sqlite3.Error, ValueError):
                session.warnings.append('Persistence failed; this run cannot be reviewed until it is rerun successfully.')
                result['status'] = 'persistence_failed'
                event(session, 'memory.write_failed')
            return result
        except InputError as exc:
            event(session, 'input.rejected', error_type='InputError')
            return {'schema_version': '1.0', 'run_id': session.run_id, 'status': 'failed',
                    'error': {'code': 'INVALID_INPUT', 'message': str(exc)}, 'trace': session.trace}
        except Exception as exc:
            event(session, 'orchestrator.failed', error_type=type(exc).__name__)
            return {'schema_version': '1.0', 'run_id': session.run_id, 'status': 'failed',
                    'error': {'code': 'EXECUTION_ERROR', 'message': 'Execution failed; inspect logs and tool configuration.'},
                    'trace': session.trace}
