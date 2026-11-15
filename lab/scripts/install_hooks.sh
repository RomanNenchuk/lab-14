#!/usr/bin/env sh
# Локальний «gate»: перед кожним git push запускає перевірки якості (на еталоні lab11) і блокує push при порушенні.
set -e
ROOT="$(git rev-parse --show-toplevel)"
LAB="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-push"
cat > "$HOOK" <<EOF
#!/usr/bin/env sh
cd "$LAB" && . .venv/bin/activate && python -m pytest quality/tests -q && python -m quality.gate --variant lab11
EOF
chmod +x "$HOOK"
echo "pre-push hook встановлено: $HOOK"
