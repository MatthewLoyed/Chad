"""Trusted completion contract and source-bound verification receipts.

This is a correctness gate, not a sandbox: repository commands run as the coder.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

GENERATED = {'.git', 'node_modules', 'dist', 'build', 'target', '__pycache__',
             '.pytest_cache', '.vite', 'coverage', 'test-results', 'playwright-report'}


def fingerprint(root):
    digest = hashlib.sha256()
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(set(dirs) - GENERATED)
        for name in sorted(files):
            path = Path(directory) / name
            data = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
            digest.update(path.relative_to(root).as_posix().encode() + b'\0' + hashlib.sha256(data).digest())
    return digest.hexdigest()


def contract_for(workspace, commands=None):
    scripts = {}
    if commands is None:
        manifest = Path(workspace) / 'package.json'
        if manifest.exists():
            declared = json.loads(manifest.read_text(encoding='utf-8-sig')).get('scripts', {})
            scripts = {k: declared[k] for k in ('lint', 'typecheck', 'build', 'test', 'test:e2e') if k in declared}
        commands = [['npm', 'run', name] for name in scripts]
    if not isinstance(commands, list) or any(
        not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg for arg in argv)
        for argv in commands
    ):
        raise ValueError('Acceptance commands must be a list of nonempty argv lists')
    return {'commands': commands, 'scripts': scripts}


class CompletionGate:
    def __init__(self, workspace, contract, artifacts):
        self.workspace = Path(workspace)
        self.contract = Path(contract)
        self.artifacts = Path(artifacts)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.receipt = self.artifacts / 'completion.json'

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
                with log.open('wb') as output:
                    result = subprocess.run(argv, cwd=self.workspace, stdout=output,
                                            stderr=subprocess.STDOUT, check=False)
                entry.update(status='passed' if result.returncode == 0 else 'failed',
                             exit_code=result.returncode, duration_s=time.monotonic()-started)
                if result.returncode:
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
