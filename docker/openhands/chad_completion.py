"""Trusted completion contract and source-bound verification receipts.

This is a correctness gate, not a sandbox: repository commands run as the coder.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

GENERATED = {'.git', 'node_modules', 'dist', 'build', 'target', '__pycache__',
             '.pytest_cache', '.vite', 'coverage', 'test-results', 'playwright-report'}
DEFAULT_CHECK_TIMEOUT_S = 600
PLAYWRIGHT_REPORT_GRACE_S = 30


def run_check(argv, workspace, output, deadline):
    env = {**os.environ, 'PLAYWRIGHT_HTML_OPEN': 'never'}
    with subprocess.Popen(argv, cwd=workspace, stdin=subprocess.DEVNULL, stdout=output,
                          stderr=subprocess.STDOUT, env=env,
                          start_new_session=os.name == 'posix') as process:
        try:
            return process.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            raise


def fingerprint(root):
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(set(dirs) - GENERATED)
        for name in sorted(files):
            path = Path(directory) / name
            data = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
            digest.update(path.relative_to(root).as_posix().encode() + b'\0' + hashlib.sha256(data).digest())
    return digest.hexdigest()


def contract_for(workspace, commands=None, timeout_s=DEFAULT_CHECK_TIMEOUT_S):
    scripts = {}
    if commands is None:
        manifest = Path(workspace) / 'package.json'
        if manifest.exists():
            declared = json.loads(manifest.read_text(encoding='utf-8-sig')).get('scripts', {})
            scripts = {k: declared[k] for k in ('lint', 'typecheck', 'build', 'test', 'test:e2e') if k in declared}
        commands = [['npm', 'run', name] for name in scripts]
        if scripts.get('test:e2e', '').strip() == 'playwright test' and timeout_s > PLAYWRIGHT_REPORT_GRACE_S:
            commands[-1] += ['--', '--global-timeout', str((timeout_s - PLAYWRIGHT_REPORT_GRACE_S) * 1000)]
    if not isinstance(commands, list) or any(
        not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg for arg in argv)
        for argv in commands
    ):
        raise ValueError('Acceptance commands must be a list of nonempty argv lists')
    if type(timeout_s) is not int or timeout_s <= 0:
        raise ValueError('Verification timeout must be a positive integer in seconds')
    return {'commands': commands, 'scripts': scripts, 'timeout_s': timeout_s}


class CompletionGate:
    def __init__(self, workspace, contract, artifacts):
        self.workspace = Path(workspace)
        self.contract = Path(contract)
        self.artifacts = Path(artifacts)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.receipt = self.artifacts / 'completion.json'

    def hook_timeout(self):
        spec = json.loads(self.contract.read_text(encoding='utf-8'))
        return len(spec['commands']) * spec.get('timeout_s', DEFAULT_CHECK_TIMEOUT_S) + PLAYWRIGHT_REPORT_GRACE_S

    def verify(self, phase='stop'):
        spec = json.loads(self.contract.read_text(encoding='utf-8'))
        before = fingerprint(self.workspace)
        contract_hash = hashlib.sha256(self.contract.read_bytes()).hexdigest()
        records = []
        run_id = uuid.uuid4().hex
        state = {'phase': phase, 'fingerprint': before, 'contract_hash': contract_hash,
                 'status': 'running', 'checks': records}

        def persist():
            temporary = self.receipt.with_suffix('.tmp')
            temporary.write_text(json.dumps(state, indent=2), encoding='utf-8')
            temporary.replace(self.receipt)

        persist()
        try:
            deadline = spec.get('timeout_s', DEFAULT_CHECK_TIMEOUT_S)
            if not spec['commands']:
                raise ValueError('No acceptance commands configured; completion is unverified')
            current = {}
            if spec.get('scripts'):
                current = json.loads((self.workspace / 'package.json').read_text(encoding='utf-8-sig')).get('scripts', {})
            if any(current.get(k) != v for k, v in spec.get('scripts', {}).items()):
                raise ValueError('Pinned check scripts changed; restore them or have the controller revise the contract')
            # Do not shell-wrap commands: pipelines cannot replace the real exit status.
            for index, argv in enumerate(spec['commands']):
                log = self.artifacts / f'{run_id}-{index}.log'
                entry = {'argv': argv, 'status': 'running', 'log': str(log)}
                records.append(entry); persist()
                started = time.monotonic()
                try:
                    with log.open('wb') as output:
                        code = run_check(argv, self.workspace, output, deadline)
                except subprocess.TimeoutExpired:
                    entry.update(status='timed_out', timeout_s=deadline, duration_s=time.monotonic()-started)
                    raise
                entry.update(status='passed' if code == 0 else 'failed',
                             exit_code=code, duration_s=time.monotonic()-started)
                if code:
                    entry['excerpt'] = log.read_text(encoding='utf-8', errors='replace')[-2000:]
                persist()
            if fingerprint(self.workspace) != before:
                state['status'] = 'stale'
            else:
                state['status'] = 'passed' if all(x['status'] == 'passed' for x in records) else 'failed'
        except Exception as exc:
            state.update(status='infra_error', error=f'{type(exc).__name__}: {exc}')
        persist()
        with (self.artifacts / 'completion-history.jsonl').open('a', encoding='utf-8') as output:
            output.write(json.dumps(state) + '\n')
        return state

    def feedback(self, state):
        return ('Completion rejected. Fix the failed checks and run them again. '
                'Do not weaken tests or claim completion from a process exit.\n' +
                json.dumps(state, ensure_ascii=False))


def main():
    # Explicit arguments survive OpenHands hook environment sanitization.
    gate = CompletionGate(*sys.argv[1:4])
    state = gate.verify()
    # Hook infrastructure failures are left to the outer controller to report.
    blocked = state['status'] in {'failed', 'stale'}
    print(json.dumps({'decision': 'deny' if blocked else 'allow',
                      'reason': gate.feedback(state) if blocked else state['status']}))


if __name__ == '__main__':
    main()
