#!/usr/bin/env bash
# Install gitleaks pre-commit hook.
# Run once from the repo root after cloning:  bash scripts/security/install_hooks.sh
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-commit"

if ! command -v gitleaks &>/dev/null; then
  echo "ERROR: gitleaks is not installed."
  echo "  Linux/WSL:  sudo apt install gitleaks"
  echo "  macOS:      brew install gitleaks"
  echo "  Windows:    see scripts/security/install_hooks.ps1"
  exit 1
fi

cat > "$HOOK_PATH" << 'HOOK'
#!/usr/bin/env bash
# Pre-commit: block secrets from entering git history.
gitleaks protect --staged --no-banner --config "$(git rev-parse --show-toplevel)/.gitleaks.toml"
HOOK

chmod +x "$HOOK_PATH"
echo "gitleaks pre-commit hook installed at $HOOK_PATH"
echo "Test it with:  gitleaks protect --staged --no-banner"
