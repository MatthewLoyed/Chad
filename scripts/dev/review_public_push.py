"""Require a local privacy review of the commits a push would publish."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


PATTERNS = {
    'email': rb'\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b',
    'money': rb'\$\s*\d[\d,.]*|\b\d[\d,.]*\s+USD\b',
    'financial detail': rb'\b(?:net worth|account balance|transactions?)\b',
    'phone': rb'(?<!\d)(?:\+1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)',
    'local path': rb'(?:[a-z]:[/\\]Users[/\\]|/home/|/Users/)[^\s/\\]+',
    'credential': rb'\b(?:gh[pousr]_[\w]{20,}|sk-(?:proj-)?[\w-]{24,}|AIza[\w-]{30,})\b',
    'binary change': rb'^Binary files .* differ$',
}


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.DEVNULL)


def outgoing(updates, remote_url):
    heads = []
    for line in updates:
        _, local_oid, _, _ = line.split()
        if set(local_oid) != {'0'}:
            git('rev-parse', '--verify', f'{local_oid}^{{commit}}')
            heads.append(local_oid)
    if not heads:
        return []
    known = []
    for row in git('ls-remote', '--refs', remote_url).decode().splitlines():
        oid, ref = row.split()
        # Restrict exclusions to advertised branch and tag history.
        if not ref.startswith(('refs/heads/', 'refs/tags/')):
            continue
        probe = subprocess.run(['git', 'rev-parse', '--verify', '--quiet', f'{oid}^{{commit}}'],
                               capture_output=True)
        if probe.returncode == 0:
            known.append(probe.stdout.decode().strip())
    tags = [oid for oid in heads if git('cat-file', '-t', oid).strip() == b'tag']
    commits = git('rev-list', *heads, '--not', *known).decode().splitlines()
    return list(dict.fromkeys([*tags, *commits]))


def scan(commits, denylist):
    blocked = False
    for commit in commits:
        if git('cat-file', '-t', commit).strip() == b'tag':
            metadata = git('cat-file', 'tag', commit)
            patch = b''
        else:
            metadata = git('show', '-s', '--format=%an%n%ae%n%cn%n%ce%n%B', commit)
            patch = git('show', '--format=', '--no-ext-diff', '--no-textconv', '--no-renames',
                        '--diff-merges=first-parent', '--root', commit)
        lines = metadata.splitlines()
        lines.extend(line[1:] for line in patch.splitlines() if line.startswith(b'+'))
        lines.extend(line for line in patch.splitlines() if line.startswith(b'Binary files '))
        counts = {name: sum(bool(re.search(pattern, line, re.I)) for line in lines)
                  for name, pattern in PATTERNS.items()}
        if denylist and any(denylist.search(line) for line in lines):
            blocked = True
            print(f'{commit[:12]}: private denylist match. Inspect locally and remove it.')
        for name, count in counts.items():
            if count:
                print(f'{commit[:12]}: {name} candidates: {count}')
    print(f'Privacy scan checked {len(commits)} commit or tag object(s).')
    return blocked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    review = modes.add_parser('review')
    review.add_argument('range', help='Git revision range, such as origin/dev..HEAD')
    review.add_argument('--tag', action='append', default=[], help='Also review an annotated tag')
    review.add_argument('--confirm-reviewed', action='store_true',
                        help='Record that you manually reviewed these exact commits for privacy')
    hook = modes.add_parser('pre-push')
    hook.add_argument('remote_url')
    args = parser.parse_args()

    common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip())
    private = common / 'private-push-patterns'
    denylist = re.compile(private.read_bytes().strip(), re.I) if private.is_file() else None
    receipt = common / 'privacy-reviewed.json'
    reviewed = set(json.loads(receipt.read_text(encoding='utf-8'))) if receipt.is_file() else set()
    if args.mode == 'review':
        commits = git('rev-list', args.range, '--').decode().splitlines()
        commits.extend(git('rev-parse', '--verify', f'refs/tags/{tag}^{{tag}}').decode().strip()
                       for tag in args.tag)
    else:
        updates = list(sys.stdin)
        if denylist and any(denylist.search(line.split()[2].encode()) for line in updates
                            if set(line.split()[1]) != {'0'}):
            print('Push blocked: a destination ref name matches the private denylist.')
            return 1
        commits = outgoing(updates, args.remote_url)
    if scan(commits, denylist):
        return 1
    if args.mode == 'review':
        if args.confirm_reviewed:
            temporary = receipt.with_suffix('.tmp')
            temporary.write_text(json.dumps(sorted(reviewed.union(commits))) + '\n', encoding='utf-8')
            temporary.replace(receipt)
            print('Recorded privacy review for the listed revision range.')
        return 0
    if missing := set(commits) - reviewed:
        print(f'Push blocked: {len(missing)} commit or tag object(s) lack a privacy review receipt.')
        print('Review the outgoing diff and commit messages, then run review_public_push.py review with --confirm-reviewed.')
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError, re.error):
        print('Privacy gate could not finish. Fix the local review setup before pushing.', file=sys.stderr)
        sys.exit(1)
