"""Exercise the installed privacy hook against a disposable local remote."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_public_push_review(tmp_path):
    source = Path(__file__).resolve().parents[2]
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1")
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    # Do not inherit author overrides from CI or a developer's shell.
    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        env.pop(key, None)

    def git(*args, cwd=repo, succeeds=True):
        result = subprocess.run(["git", *args], cwd=cwd, env=env,
                                capture_output=True, text=True)
        assert (result.returncode == 0) == succeeds, result.stdout + result.stderr
        return result.stdout + result.stderr

    git("init", "--bare", str(remote), cwd=tmp_path)
    git("init", str(repo), cwd=tmp_path)
    git("config", "user.email", "fixture@example.test")
    git("config", "user.name", "Fixture Example")
    sample = repo / "sample.txt"
    sample.write_text("public fixture", encoding="utf-8")
    git("add", "sample.txt")
    git("commit", "-m", "Base")
    git("push", str(remote), "HEAD:refs/heads/main")
    for name in (".githooks/pre-push", "scripts/dev/review_public_push.py"):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    (repo / ".githooks/pre-push").chmod(0o755)
    git("config", "core.hooksPath", ".githooks")
    base = git("rev-parse", "HEAD").strip()

    def review(*args):
        result = subprocess.run([sys.executable, "scripts/dev/review_public_push.py",
                                 "review", *args], cwd=repo, env=env,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    sample.write_text("synthetic amount: $12000", encoding="utf-8")
    git("add", "sample.txt")
    git("commit", "-m", "Update fictional example")
    assert "lack a privacy review receipt" in git(
        "push", str(remote), "HEAD:refs/heads/main", succeeds=False)
    assert "money candidates" in review(base + "..HEAD", "--confirm-reviewed")
    git("push", str(remote), "HEAD:refs/heads/main")
    git("tag", "-a", "v0-fixture", "-m", "Synthetic tag")
    assert "lack a privacy review receipt" in git(
        "push", str(remote), "v0-fixture", succeeds=False)
    review("HEAD..HEAD", "--tag", "v0-fixture", "--confirm-reviewed")
    git("push", str(remote), "v0-fixture")
    (repo / ".git/private-push-patterns").write_text("fictional-deny-marker", encoding="utf-8")
    sample.write_text("fictional-deny-marker", encoding="utf-8")
    git("add", "sample.txt")
    git("commit", "-m", "Introduce a fixture marker")
    sample.write_text("public fixture", encoding="utf-8")
    git("add", "sample.txt")
    git("commit", "-m", "Remove the marker from the tip")
    output = git("push", str(remote), "HEAD:refs/heads/main", succeeds=False)
    assert "private denylist match" in output
    assert "fictional-deny-marker" not in output
