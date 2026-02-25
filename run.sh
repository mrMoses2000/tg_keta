#!/usr/bin/env bash
# ============================================================
# run.sh — Master control script for Keto Telegram Bot
#
# Usage:
#   ./run.sh doctor     - Check all dependencies and environment
#   ./run.sh env        - Create .env from .env.example
#   ./run.sh up         - Start services (Docker + bot processes)
#   ./run.sh down       - Stop services
#   ./run.sh logs       - Tail logs
#   ./run.sh migrate    - Apply local Postgres migrations
#   ./run.sh test       - Run tests (unit/integration/e2e/smoke)
#   ./run.sh seed       - Seed test data
#   ./run.sh tagify     - Batch recipe enrichment (future)
#
# OS auto-detect: reads /etc/os-release and uname -m
# Idempotent: safe to run multiple times
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_ok()   { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $1"; }
log_err()  { echo -e "${RED}✗${NC} $1"; }
log_info() { echo -e "${BLUE}ℹ${NC} $1"; }

# ─── OS Auto-Detect ─────────────────────────────────────────

detect_os() {
    OS_NAME="unknown"
    OS_VERSION="unknown"
    OS_ARCH="$(uname -m)"
    OS_KERNEL="$(uname -s)"

    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        . /etc/os-release
        OS_NAME="${NAME:-unknown}"
        OS_VERSION="${VERSION_ID:-unknown}"
    elif [[ "$OS_KERNEL" == "Darwin" ]]; then
        OS_NAME="macOS"
        OS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo 'unknown')"
    fi

    log_info "OS: $OS_NAME $OS_VERSION ($OS_ARCH, kernel: $OS_KERNEL)"
}

# ─── Doctor ──────────────────────────────────────────────────

cmd_doctor() {
    echo "🩺 Running health checks..."
    echo ""
    detect_os

    local ok=0
    local warn=0
    local fail=0

    # Python
    if command -v python3 &>/dev/null; then
        PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 10 ]]; then
            log_ok "Python $PY_VERSION (>= 3.10 required)"
            ((ok++))
        else
            log_err "Python $PY_VERSION — need >= 3.10"
            ((fail++))
        fi
    else
        log_err "Python3 not found"
        ((fail++))
    fi

    # pip
    if python3 -m pip --version &>/dev/null; then
        log_ok "pip available"
        ((ok++))
    else
        log_err "pip not found (python3 -m pip)"
        ((fail++))
    fi

    # Docker
    if command -v docker &>/dev/null; then
        if docker info &>/dev/null; then
            log_ok "Docker running"
            ((ok++))
        else
            log_warn "Docker installed but not running"
            ((warn++))
        fi
    else
        log_warn "Docker not installed (needed for Redis/Postgres)"
        ((warn++))
    fi

    # docker compose (v2)
    if docker compose version &>/dev/null; then
        log_ok "docker compose available"
        ((ok++))
    elif command -v docker-compose &>/dev/null; then
        log_ok "docker-compose (v1) available"
        ((ok++))
    else
        log_warn "docker compose not found"
        ((warn++))
    fi

    # Redis connectivity
    if command -v redis-cli &>/dev/null; then
        if redis-cli ping &>/dev/null; then
            log_ok "Redis reachable"
            ((ok++))
        else
            log_warn "Redis not reachable (will use Docker)"
            ((warn++))
        fi
    else
        log_info "redis-cli not installed (OK if using Docker)"
    fi

    # Postgres connectivity
    if command -v psql &>/dev/null; then
        log_ok "psql available"
        ((ok++))
    else
        log_info "psql not installed (OK if using Docker)"
    fi

    # .env file
    if [[ -f .env ]]; then
        log_ok ".env file exists"
        ((ok++))

        # Check required vars
        for var in TELEGRAM_BOT_TOKEN SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY; do
            if grep -q "^${var}=" .env && ! grep -q "^${var}=your_" .env; then
                log_ok "  $var is set"
            else
                log_warn "  $var not configured"
                ((warn++))
            fi
        done
    else
        log_warn ".env file not found (run: ./run.sh env)"
        ((warn++))
    fi

    # Gemini CLI (if on Linux)
    if [[ "$OS_KERNEL" == "Linux" ]]; then
        if command -v gemini &>/dev/null; then
            log_ok "Gemini CLI found"
            ((ok++))
        else
            log_warn "Gemini CLI not found in PATH"
            ((warn++))
        fi
    fi

    # Ports check
    for port in 8080 5432 6379; do
        if lsof -i ":$port" &>/dev/null 2>&1 || ss -tlnp 2>/dev/null | grep -q ":$port "; then
            log_warn "Port $port is in use"
            ((warn++))
        else
            log_ok "Port $port is free"
            ((ok++))
        fi
    done

    echo ""
    echo "────────────────────────────────────"
    echo -e "Results: ${GREEN}$ok OK${NC}, ${YELLOW}$warn warnings${NC}, ${RED}$fail failures${NC}"

    if [[ $fail -gt 0 ]]; then
        echo -e "${RED}Fix failures before proceeding.${NC}"
        return 1
    fi
    return 0
}

# ─── Env ─────────────────────────────────────────────────────

cmd_env() {
    if [[ -f .env ]]; then
        log_warn ".env already exists. Overwrite? (y/N)"
        read -r answer
        if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
            log_info "Keeping existing .env"
            return 0
        fi
    fi

    cp .env.example .env
    log_ok "Created .env from .env.example"

    # Generate webhook secret
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
    sed -i.bak "s/generate_a_random_string_here/$SECRET/" .env && rm -f .env.bak
    log_ok "Generated TELEGRAM_WEBHOOK_SECRET"

    echo ""
    log_info "Now edit .env and fill in:"
    log_info "  TELEGRAM_BOT_TOKEN   — from @BotFather"
    log_info "  SUPABASE_SERVICE_ROLE_KEY — from Supabase dashboard"
    echo ""
}

# ─── Up ──────────────────────────────────────────────────────

cmd_up() {
    log_info "Starting services..."

    # Start Docker services
    if docker compose version &>/dev/null; then
        docker compose up -d
    elif command -v docker-compose &>/dev/null; then
        docker-compose up -d
    else
        log_err "docker compose not found"
        return 1
    fi
    log_ok "Docker services started (Redis + Postgres)"

    # Wait for services
    log_info "Waiting for services to be ready..."
    sleep 3

    # Apply local migrations
    cmd_migrate

    # Install Python deps if needed
    if [[ ! -d ".venv" ]]; then
        log_info "Creating virtual environment..."
        python3 -m venv .venv
    fi
    # shellcheck source=/dev/null
    source .venv/bin/activate
    pip install -q -r requirements.txt

    # Start webhook and worker as background processes
    log_info "Starting webhook server..."
    python3 run_webhook.py &
    WEBHOOK_PID=$!
    echo "$WEBHOOK_PID" > .webhook.pid
    log_ok "Webhook started (PID: $WEBHOOK_PID)"

    log_info "Starting worker..."
    python3 run_worker.py &
    WORKER_PID=$!
    echo "$WORKER_PID" > .worker.pid
    log_ok "Worker started (PID: $WORKER_PID)"

    echo ""
    log_ok "All services running. Use ./run.sh logs to see output."
}

# ─── Down ────────────────────────────────────────────────────

cmd_down() {
    log_info "Stopping services..."

    # Stop Python processes
    for pidfile in .webhook.pid .worker.pid; do
        if [[ -f "$pidfile" ]]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid"
                log_ok "Stopped process $pid"
            fi
            rm -f "$pidfile"
        fi
    done

    # Stop Docker
    if docker compose version &>/dev/null; then
        docker compose down
    elif command -v docker-compose &>/dev/null; then
        docker-compose down
    fi
    log_ok "Services stopped"
}

# ─── Logs ────────────────────────────────────────────────────

cmd_logs() {
    echo "Tailing logs (Ctrl+C to stop)..."
    if docker compose version &>/dev/null; then
        docker compose logs -f --tail=50
    elif command -v docker-compose &>/dev/null; then
        docker-compose logs -f --tail=50
    fi
}

# ─── Migrate ─────────────────────────────────────────────────

cmd_migrate() {
    log_info "Applying local Postgres migrations..."

    # Source .env for connection vars
    if [[ -f .env ]]; then
        set -a
        # shellcheck source=/dev/null
        source .env
        set +a
    fi

    local PG_HOST="${POSTGRES_HOST:-localhost}"
    local PG_PORT="${POSTGRES_PORT:-5432}"
    local PG_DB="${POSTGRES_DB:-keto_bot}"
    local PG_USER="${POSTGRES_USER:-keto_bot}"

    export PGPASSWORD="${POSTGRES_PASSWORD:-change_me_in_production}"

    # Wait for Postgres
    local retries=10
    while ! psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1" &>/dev/null; do
        ((retries--))
        if [[ $retries -le 0 ]]; then
            log_err "Postgres not reachable at $PG_HOST:$PG_PORT"
            return 1
        fi
        sleep 1
    done

    # Apply migrations (002-005 are for local Postgres)
    for migration in migrations/002_*.sql migrations/003_*.sql migrations/004_*.sql migrations/005_*.sql; do
        if [[ -f "$migration" ]]; then
            log_info "Applying: $migration"
            psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -f "$migration" 2>&1 | grep -v "^NOTICE" || true
            log_ok "Applied: $(basename "$migration")"
        fi
    done

    unset PGPASSWORD
    log_ok "Local migrations complete"
}

# ─── Test ────────────────────────────────────────────────────

cmd_test() {
    local test_type="${1:-unit}"

    # Ensure venv
    if [[ -d ".venv" ]]; then
        # shellcheck source=/dev/null
        source .venv/bin/activate
    fi

    case "$test_type" in
        unit)
            log_info "Running unit tests..."
            python3 -m pytest tests/unit/ -v --tb=short
            ;;
        integration)
            log_info "Running integration tests (requires Docker)..."
            python3 -m pytest tests/integration/ -v --tb=short
            ;;
        e2e)
            log_info "Running e2e tests..."
            python3 -m pytest tests/e2e/ -v --tb=short
            ;;
        smoke)
            log_info "Running smoke tests..."
            cmd_doctor
            # Basic connectivity tests
            if curl -sf http://localhost:8080/health &>/dev/null; then
                log_ok "Webhook health check passed"
            else
                log_err "Webhook not reachable"
                return 1
            fi
            ;;
        all)
            cmd_test unit
            cmd_test integration
            ;;
        *)
            log_err "Unknown test type: $test_type"
            echo "Usage: ./run.sh test [unit|integration|e2e|smoke|all]"
            return 1
            ;;
    esac
}

# ─── Seed ────────────────────────────────────────────────────

cmd_seed() {
    log_info "Seeding is handled via Supabase. Use the web app admin."
    log_info "For local Postgres test data, run: pytest tests/integration/conftest.py"
}

# ─── Tagify ──────────────────────────────────────────────────

cmd_tagify() {
    log_info "Recipe enrichment (tagify) not yet implemented."
    log_info "This will batch-populate recipe_tags table."
    log_info "Planned for next iteration."
}

# ─── Main ────────────────────────────────────────────────────

cmd="${1:-help}"

case "$cmd" in
    doctor)  cmd_doctor ;;
    env)     cmd_env ;;
    up)      cmd_up ;;
    down)    cmd_down ;;
    logs)    cmd_logs ;;
    migrate) cmd_migrate ;;
    test)    cmd_test "${2:-unit}" ;;
    seed)    cmd_seed ;;
    tagify)  cmd_tagify ;;
    help|*)
        echo "🥑 Keto Telegram Bot — run.sh"
        echo ""
        echo "Usage: ./run.sh <command>"
        echo ""
        echo "Commands:"
        echo "  doctor     Check dependencies and environment"
        echo "  env        Create .env from template"
        echo "  up         Start all services"
        echo "  down       Stop all services"
        echo "  logs       Tail Docker logs"
        echo "  migrate    Apply local Postgres migrations"
        echo "  test       Run tests [unit|integration|e2e|smoke|all]"
        echo "  seed       Seed test data"
        echo "  tagify     Batch recipe enrichment"
        echo "  help       Show this help"
        ;;
esac
