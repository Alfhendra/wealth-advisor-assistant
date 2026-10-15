"""CLI composition root: concrete adapters are wired here, not inside agents."""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .agents import AdvisoryAgent, AnalyzerAgent, DataFetcherAgent, Orchestrator
from .analytics import FinancialDataTool, RuleAnalysisTool
from .tools import HttpCRMTool, MockCRMTool, SQLiteMemory


def main(argv=None):
    # --- Commands and options ---
    parser = argparse.ArgumentParser(description='Wealth Advisor Assistant - synthetic demo')
    parser.add_argument('--db', default='data/advisor.sqlite3')
    parser.add_argument('--log', default='data/execution.jsonl')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run')
    run.add_argument('input')
    run.add_argument('--output')
    crm = run.add_mutually_exclusive_group()
    crm.add_argument('--crm-url')
    crm.add_argument('--crm-fail', action='store_true')
    review = commands.add_parser('review')
    review.add_argument('run_id')
    review.add_argument('--reviewer', required=True)
    review.add_argument('--decision', choices=['approve', 'reject', 'override'], required=True)
    review.add_argument('--note', required=True)
    show = commands.add_parser('show')
    show.add_argument('run_id')
    args = parser.parse_args(argv)

    # --- Local resources: one log handler and database adapter per invocation ---
    try:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(args.log, encoding='utf-8')
        logger = logging.getLogger('wealth_advisor')
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            memory = SQLiteMemory(args.db)
            if args.command == 'run':
                source = Path(args.input)
                if source.stat().st_size > 5_000_000:
                    raise ValueError('Input exceeds 5 MB limit')
                raw = json.loads(source.read_text(encoding='utf-8-sig'))
                # Wire adapters here so agents remain independent of transport and storage.
                crm_tool = HttpCRMTool(args.crm_url) if args.crm_url else MockCRMTool(args.crm_fail)
                system = Orchestrator(DataFetcherAgent(FinancialDataTool(), crm_tool),
                                      AnalyzerAgent(RuleAnalysisTool()), AdvisoryAgent(), memory)
                result = system.run(raw)
            elif args.command == 'review':
                result = memory.review(args.run_id, args.reviewer, args.decision, args.note)
                logger.info(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(), 'run_id': args.run_id, 'event': 'review.recorded', 'decision': args.decision}))
            else:
                result = memory.get(args.run_id)

            # --- Output contract: stdout stays valid JSON for scripts and reviewers ---
            output = json.dumps(result, indent=2, allow_nan=False)
            if getattr(args, 'output', None):
                Path(args.output).parent.mkdir(parents=True, exist_ok=True)
                Path(args.output).write_text(output + '\n', encoding='utf-8')
            print(output)
            return 1 if result['status'] in ('failed', 'persistence_failed') else 0
        finally:
            logger.removeHandler(handler)
            handler.close()
    except Exception as exc:
        # Do not expose filesystem paths, credentials or untrusted server messages.
        print(json.dumps({'status': 'failed', 'error': {'code': 'CLI_ERROR', 'type': type(exc).__name__,
                          'message': 'Check input JSON, arguments, run state, and writable output/database paths.'}}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
