"""Regenerate sample artifacts through the public CLI in an isolated temporary DB."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as temp:
        prefix = [sys.executable, '-m', 'wealth_advisor', '--db', str(Path(temp) / 'samples.sqlite3'),
                  '--log', str(Path(temp) / 'execution.jsonl')]
        def invoke(args, filename, expected_status, expected_code=0):
            proc = subprocess.run(prefix + args, cwd=ROOT, text=True, capture_output=True, encoding='utf-8')
            assert proc.returncode == expected_code, proc.stdout + proc.stderr
            result = json.loads(proc.stdout)
            assert result['status'] == expected_status, result
            (ROOT / 'samples' / filename).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
            return result
        first = invoke(['run', 'samples/client.json'], 'output.json', 'pending_review')
        assert first['previous_summary'] is None
        second = invoke(['run', 'samples/client.json'], 'output_with_memory.json', 'pending_review')
        assert second['previous_summary']['run_id'] == first['run_id']
        fallback = invoke(['run', 'samples/client.json', '--crm-fail'], 'output_crm_failure.json', 'pending_review')
        assert fallback['crm_context']['risk_tolerance'] == 'unknown'
        invoke(['run', 'samples/invalid_client.json'], 'output_invalid.json', 'failed', 1)
        reviewed = invoke(['review', first['run_id'], '--reviewer', 'Demo Advisor', '--decision', 'override',
                          '--note', 'Synthetic example: large transfer confirmed as planned; retain remaining items for follow-up.'],
                         'output_reviewed.json', 'overridden')
        shown = subprocess.run(prefix + ['show', first['run_id']], cwd=ROOT, text=True, capture_output=True, encoding='utf-8')
        assert shown.returncode == 0 and json.loads(shown.stdout) == reviewed
        repeated = subprocess.run(prefix + ['review', first['run_id'], '--reviewer', 'Demo Advisor',
                                             '--decision', 'approve', '--note', 'Repeat'], cwd=ROOT, capture_output=True)
        assert repeated.returncode == 1
        (ROOT / 'samples' / 'execution.jsonl').write_text((Path(temp) / 'execution.jsonl').read_text(encoding='utf-8'), encoding='utf-8')
    print('CLI verification passed: first run, memory, CRM fallback, invalid input, override, show, repeat-review rejection.')


if __name__ == '__main__':
    main()
