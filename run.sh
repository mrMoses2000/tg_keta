#!/usr/bin/env bash
# ============================================================
# run.sh — Master control script for Keto Telegram Bot
#
# Usage:
#   ./run.sh setup      - Interactive setup wizard (first run)
#   ./run.sh doctor     - Check all dependencies and environment
#   ./run.sh env        - Create/edit .env interactively
#   ./run.sh install    - Install missing dependencies interactively
#   ./run.sh up         - Start services (Docker + bot processes)
#   ./run.sh down       - Stop services
#   ./run.sh webhook    - Register/update Telegram webhook
#   ./run.sh tunnel     - Manage ngrok tunnel (up/down/status/url)
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
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log_ok()   { echo -e "${GREEN}✓${NC} $1"; }
log_warn() { echo -e "${YELLOW}⚠${NC} $1"; }
log_err()  { echo -e "${RED}✗${NC} $1"; }
log_info() { echo -e "${BLUE}ℹ${NC} $1"; }
log_step() { echo -e "\n${CYAN}${BOLD}── $1 ──${NC}\n"; }

APP_LOG_DIR="${SCRIPT_DIR}/.logs"
WEBHOOK_LOG_FILE="${APP_LOG_DIR}/webhook.log"
WORKER_LOG_FILE="${APP_LOG_DIR}/worker.log"
NGROK_LOG_FILE="${APP_LOG_DIR}/ngrok.log"
NGROK_PID_FILE="${SCRIPT_DIR}/.ngrok.pid"
NGROK_PUBLIC_URL=""

load_env() {
    if [[ -f .env ]]; then
        # shellcheck source=/dev/null
        set -a; source .env 2>/dev/null; set +a
    fi
}

docker_compose() {
    if docker compose version &>/dev/null 2>&1; then
        docker compose "$@"
    elif command -v docker-compose &>/dev/null; then
        docker-compose "$@"
    else
        return 127
    fi
}

stop_pidfile_process() {
    local pidfile="$1"
    local name="$2"

    if [[ ! -f "$pidfile" ]]; then
        return 0
    fi

    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -z "$pid" ]]; then
        rm -f "$pidfile"
        return 0
    fi

    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        for _ in {1..10}; do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "$name не завершился по SIGTERM, отправляю SIGKILL"
            kill -9 "$pid" 2>/dev/null || true
        fi
        log_ok "$name остановлен (PID: $pid)"
    fi

    rm -f "$pidfile"
}

start_managed_process() {
    local name="$1"
    local pidfile="$2"
    local logfile="$3"
    shift 3

    mkdir -p "$APP_LOG_DIR"
    touch "$logfile"

    if [[ -f "$pidfile" ]]; then
        local existing_pid
        existing_pid="$(cat "$pidfile" 2>/dev/null || true)"
        if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
            if prompt_yn "$name уже запущен (PID: $existing_pid). Перезапустить?" "n"; then
                stop_pidfile_process "$pidfile" "$name"
            else
                log_info "$name оставлен запущенным"
                return 0
            fi
        else
            rm -f "$pidfile"
        fi
    fi

    log_info "Запускаю $name..."
    nohup "$@" >> "$logfile" 2>&1 &
    local pid=$!
    echo "$pid" > "$pidfile"
    sleep 1

    if kill -0 "$pid" 2>/dev/null; then
        log_ok "$name запущен (PID: $pid)"
        return 0
    fi

    log_err "Не удалось запустить $name. Проверьте лог: $logfile"
    return 1
}

ensure_venv_ready() {
    if [[ -z "${OS_KERNEL:-}" ]]; then
        detect_os
    fi

    if [[ ! -d ".venv" ]]; then
        ensure_python_venv_support
        log_info "Создаю Python virtualenv..."
        if ! python3 -m venv .venv; then
            log_warn "Не удалось создать virtualenv с первого раза"
            if prompt_yn "Попробовать доустановить python3-venv и повторить?" "y"; then
                install_python_venv_support || {
                    log_err "Не удалось установить пакет python3-venv"
                    return 1
                }
                python3 -m venv .venv || {
                    log_err "Создать .venv не удалось даже после установки python3-venv"
                    return 1
                }
            else
                return 1
            fi
        fi
    fi

    # shellcheck source=/dev/null
    source .venv/bin/activate
    if ! python -m pip --version &>/dev/null; then
        log_warn "pip внутри .venv не найден, пробую восстановить через ensurepip..."
        python -m ensurepip --upgrade || {
            log_err "Не удалось восстановить pip в .venv"
            return 1
        }
    fi

    log_info "Устанавливаю Python-зависимости (pip install -r requirements.txt)..."
    python -m pip install -q -r requirements.txt
}

install_python_venv_support() {
    if [[ -z "${OS_KERNEL:-}" ]]; then
        detect_os
    fi

    if [[ "$OS_KERNEL" == "Darwin" ]]; then
        # Homebrew python usually includes venv support.
        return 0
    fi

    if [[ "$OS_KERNEL" == "Linux" ]] && command -v apt-get &>/dev/null; then
        local version_pkg
        version_pkg="$(python3 -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}-venv")' 2>/dev/null || true)"
        log_info "Устанавливаю поддержку venv через apt..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3-venv && return 0
        [[ -n "$version_pkg" ]] && sudo apt-get install -y -qq "$version_pkg" && return 0
        return 1
    fi

    if [[ "$OS_KERNEL" == "Linux" ]] && command -v dnf &>/dev/null; then
        log_info "Устанавливаю поддержку venv через dnf..."
        sudo dnf install -y python3-venv || sudo dnf install -y python3
        return $?
    fi

    return 1
}

ensure_python_venv_support() {
    if python3 -m ensurepip --version &>/dev/null 2>&1; then
        return 0
    fi

    log_warn "Модуль ensurepip недоступен (обычно отсутствует python3-venv)"
    if prompt_yn "Установить пакет поддержки virtualenv сейчас?" "y"; then
        install_python_venv_support || {
            log_err "Не удалось установить пакеты для virtualenv"
            return 1
        }
        if python3 -m ensurepip --version &>/dev/null 2>&1; then
            log_ok "Поддержка virtualenv установлена"
            return 0
        fi
        log_err "ensurepip всё ещё недоступен после установки"
        return 1
    fi

    log_err "Без virtualenv (python3-venv) запуск проекта невозможен"
    return 1
}

get_ngrok_public_url() {
    local url=""
    url="$(curl -sf http://127.0.0.1:4040/api/tunnels 2>/dev/null | \
        python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((t.get("public_url","") for t in d.get("tunnels",[]) if t.get("proto")=="https"), ""))' 2>/dev/null || true)"
    echo "$url"
}

start_ngrok_tunnel() {
    load_env
    local port="${WEBHOOK_PORT:-8080}"

    if ! command -v ngrok &>/dev/null; then
        log_err "ngrok не найден. Установите через ./run.sh install или вручную"
        return 1
    fi

    if ! start_managed_process "ngrok" "$NGROK_PID_FILE" "$NGROK_LOG_FILE" ngrok http "$port"; then
        return 1
    fi

    local retries=15
    local url=""
    while [[ $retries -gt 0 ]]; do
        url="$(get_ngrok_public_url)"
        if [[ -n "$url" ]]; then
            NGROK_PUBLIC_URL="$url"
            set_env_var "WEBHOOK_MODE" "ngrok"
            set_env_var "WEBHOOK_DOMAIN" "$url"
            log_ok "ngrok tunnel поднят: $url"
            return 0
        fi
        retries=$((retries - 1))
        sleep 1
    done

    log_err "ngrok запущен, но URL не получен. Проверьте: $NGROK_LOG_FILE"
    return 1
}

stop_ngrok_tunnel() {
    stop_pidfile_process "$NGROK_PID_FILE" "ngrok"
}

# Helper: prompt with a default value
prompt_with_default() {
    local prompt_text="$1"
    local default_val="$2"
    local var_name="$3"

    if [[ -n "$default_val" ]]; then
        echo -en "${BLUE}$prompt_text${NC} [${GREEN}$default_val${NC}]: "
    else
        echo -en "${BLUE}$prompt_text${NC}: "
    fi
    read -r input
    if [[ -z "$input" ]]; then
        eval "$var_name='$default_val'"
    else
        eval "$var_name='$input'"
    fi
}

# Helper: prompt for a secret (no echo)
prompt_secret() {
    local prompt_text="$1"
    local var_name="$2"

    echo -en "${BLUE}$prompt_text${NC}: "
    read -rs input
    echo ""
    eval "$var_name='$input'"
}

# Helper: yes/no prompt
prompt_yn() {
    local prompt_text="$1"
    local default="${2:-n}"

    if [[ "$default" == "y" ]]; then
        echo -en "${BLUE}$prompt_text${NC} [${GREEN}Y${NC}/n]: "
    else
        echo -en "${BLUE}$prompt_text${NC} [y/${GREEN}N${NC}]: "
    fi
    read -r answer
    answer="${answer:-$default}"
    [[ "$answer" == "y" || "$answer" == "Y" ]]
}

# Helper: update a variable in .env
set_env_var() {
    local key="$1"
    local value="$2"
    local envfile="${3:-.env}"

    if grep -q "^${key}=" "$envfile" 2>/dev/null; then
        # Use a different sed delimiter to handle URLs and special chars
        sed -i.bak "s|^${key}=.*|${key}=${value}|" "$envfile" && rm -f "${envfile}.bak"
    else
        echo "${key}=${value}" >> "$envfile"
    fi
}

# Helper: get a variable from .env
get_env_var() {
    local key="$1"
    local envfile="${2:-.env}"

    if [[ -f "$envfile" ]]; then
        grep "^${key}=" "$envfile" 2>/dev/null | head -1 | cut -d'=' -f2-
    fi
}

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

# ─── Package manager helper ─────────────────────────────────

install_package() {
    local pkg="$1"
    local desc="${2:-$pkg}"

    if [[ -z "${OS_KERNEL:-}" ]]; then
        detect_os
    fi

    if [[ "$OS_KERNEL" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            log_info "Устанавливаю $desc через brew..."
            brew install "$pkg"
        else
            log_err "Homebrew не найден. Установите: https://brew.sh"
            return 1
        fi
    elif [[ "$OS_KERNEL" == "Linux" ]]; then
        if command -v apt-get &>/dev/null; then
            log_info "Устанавливаю $desc через apt..."
            sudo apt-get update -qq && sudo apt-get install -y -qq "$pkg"
        elif command -v dnf &>/dev/null; then
            log_info "Устанавливаю $desc через dnf..."
            sudo dnf install -y "$pkg"
        else
            log_err "Не найден менеджер пакетов (apt/dnf/brew)"
            return 1
        fi
    fi
}

# ============================================================
#                    SETUP — Interactive Wizard
# ============================================================

cmd_setup() {
    echo ""
    echo -e "${BOLD}🥑 КетоБот — Мастер первоначальной настройки${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "Порядок настройки:"
    echo "  1. Скачать и установить ВСЕ зависимости"
    echo "  2. Заполнить переменные окружения (.env)"
    echo "  3. Запустить контейнеры (Redis + Postgres)"
    echo "  4. Применить миграции БД"
    echo "  5. Зарегистрировать Telegram вебхук"
    echo ""

    detect_os

    if ! prompt_yn "Начать настройку?" "y"; then
        echo "Отменено."
        return 0
    fi

    # ── Step 1: Install ALL dependencies ──
    log_step "Шаг 1/5: Скачивание и установка зависимостей"
    echo "Проверяю и устанавливаю всё необходимое..."
    echo ""
    setup_install_all

    # ── Step 2: Configure .env ──
    log_step "Шаг 2/5: Настройка переменных окружения"
    echo "Все программы установлены. Теперь заполним настройки."
    echo ""
    setup_env_interactive

    # ── Step 3: Start Docker containers ──
    log_step "Шаг 3/5: Запуск контейнеров (Redis + Postgres)"
    setup_docker

    # ── Step 4: Apply migrations ──
    log_step "Шаг 4/5: Миграции базы данных"
    setup_migrations

    # ── Step 5: Register webhook ──
    log_step "Шаг 5/5: Регистрация Telegram вебхука"
    setup_webhook_interactive

    # ── Summary ──
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "${GREEN}${BOLD}✓ Настройка завершена!${NC}"
    echo ""
    echo "Следующие шаги:"
    echo "  ./run.sh up        — запустить бота"
    echo "  ./run.sh test unit — прогнать тесты"
    echo "  ./run.sh doctor    — проверить состояние"
    echo "  ./run.sh logs      — посмотреть логи"
    echo ""
}

# ─── Step 1: Install ALL dependencies ────────────────────────
# Order: system packages → Docker → Node.js → Python venv → Gemini CLI → ngrok
# NOTHING is started here — only downloaded and installed.

setup_install_all() {
    echo -e "${BOLD}1.1 Системные пакеты${NC}"
    echo ""

    # ── Python 3.10+ ──
    if command -v python3 &>/dev/null; then
        PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [[ "$PY_MINOR" -ge 10 ]]; then
            log_ok "Python $PY_VERSION"
        else
            log_err "Python $PY_VERSION — нужен >= 3.10"
            if prompt_yn "Попробовать установить Python 3.12?"; then
                if [[ "$OS_KERNEL" == "Darwin" ]]; then
                    install_package python@3.12 "Python 3.12" || true
                else
                    sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv || true
                fi
            fi
        fi
    else
        log_err "Python3 не найден"
        if prompt_yn "Установить Python?"; then
            if [[ "$OS_KERNEL" == "Darwin" ]]; then
                install_package python@3.12 "Python 3.12" || true
            else
                sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv || true
            fi
        fi
    fi

    # ── pip ──
    if python3 -m pip --version &>/dev/null 2>&1; then
        log_ok "pip"
    else
        log_warn "pip не найден"
        if prompt_yn "Установить pip?"; then
            if [[ "$OS_KERNEL" == "Darwin" ]]; then
                python3 -m ensurepip 2>/dev/null || install_package python@3.12 "Python + pip" || true
            else
                sudo apt-get install -y -qq python3-pip 2>/dev/null || true
            fi
        fi
    fi

    # ── psql (PostgreSQL client) ──
    if command -v psql &>/dev/null; then
        log_ok "psql"
    else
        log_warn "psql не найден (нужен для миграций)"
        if prompt_yn "Установить postgresql-client?"; then
            if [[ "$OS_KERNEL" == "Darwin" ]]; then
                install_package libpq "PostgreSQL client" || true
            else
                sudo apt-get install -y -qq postgresql-client || true
            fi
        fi
    fi

    # ── Docker ──
    echo ""
    echo -e "${BOLD}1.2 Docker${NC}"
    echo ""

    if command -v docker &>/dev/null; then
        if docker info &>/dev/null 2>&1; then
            log_ok "Docker работает"
        else
            log_warn "Docker установлен, но не запущен"
            echo "  Запустите Docker Desktop или сервис docker:"
            if [[ "$OS_KERNEL" == "Darwin" ]]; then
                echo "    open -a Docker"
            else
                echo "    sudo systemctl start docker"
            fi
            if prompt_yn "Попробовать запустить Docker сейчас?"; then
                if [[ "$OS_KERNEL" == "Darwin" ]]; then
                    open -a Docker 2>/dev/null || true
                    echo "  Подождите, пока Docker Desktop запустится..."
                    sleep 10
                else
                    sudo systemctl start docker 2>/dev/null || true
                fi
            fi
        fi
    else
        log_warn "Docker не установлен"
        echo "  Docker нужен для Redis и PostgreSQL."
        echo ""
        if [[ "$OS_KERNEL" == "Darwin" ]]; then
            echo "  Варианты установки:"
            echo "    1. Docker Desktop: https://docs.docker.com/desktop/install/mac-install/"
            echo "    2. brew install --cask docker"
            echo ""
            if prompt_yn "Установить через brew?"; then
                brew install --cask docker 2>/dev/null || true
                echo "  Запустите Docker Desktop после установки."
            fi
        else
            echo "  Варианты установки:"
            echo "    1. Официальный скрипт: curl -fsSL https://get.docker.com | sh"
            echo "    2. apt: sudo apt install docker.io docker-compose-plugin"
            echo ""
            if prompt_yn "Установить через официальный скрипт?"; then
                curl -fsSL https://get.docker.com | sh
                sudo usermod -aG docker "$USER" 2>/dev/null || true
                sudo systemctl start docker 2>/dev/null || true
                log_ok "Docker установлен"
                log_warn "Может потребоваться перелогиниться для группы docker"
            fi
        fi
    fi

    # docker compose
    if docker compose version &>/dev/null 2>&1; then
        log_ok "docker compose"
    elif command -v docker-compose &>/dev/null; then
        log_ok "docker-compose (v1)"
    else
        log_warn "docker compose не найден"
        if [[ "$OS_KERNEL" == "Linux" ]] && command -v apt-get &>/dev/null; then
            if prompt_yn "Установить docker-compose-plugin?"; then
                sudo apt-get install -y -qq docker-compose-plugin 2>/dev/null || true
            fi
        fi
    fi

    # ── Node.js ──
    echo ""
    echo -e "${BOLD}1.3 Node.js (для Gemini CLI)${NC}"
    echo ""

    if command -v node &>/dev/null; then
        log_ok "Node.js $(node --version)"
    else
        log_warn "Node.js не найден (нужен для Gemini CLI)"
        if prompt_yn "Установить Node.js 20 LTS?"; then
            if [[ "$OS_KERNEL" == "Darwin" ]]; then
                install_package node "Node.js" || true
            else
                log_info "Скачиваю и устанавливаю Node.js 20 LTS..."
                curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null || true
                sudo apt-get install -y -qq nodejs 2>/dev/null || true
            fi
            if command -v node &>/dev/null; then
                log_ok "Node.js $(node --version) установлен"
            fi
        fi
    fi

    # ── Gemini CLI ──
    echo ""
    echo -e "${BOLD}1.4 Gemini CLI (LLM)${NC}"
    echo ""

    if command -v gemini &>/dev/null; then
        log_ok "Gemini CLI уже установлен"
    else
        echo "  Gemini CLI — инструмент для вызова Google Gemini AI."
        echo "  Бот использует его для генерации ответов пользователям."
        echo ""

        if command -v npm &>/dev/null; then
            if prompt_yn "Установить Gemini CLI через npm?" "y"; then
                log_info "Устанавливаю @google/gemini-cli..."
                npm install -g @google/gemini-cli 2>/dev/null || {
                    log_warn "npm install не сработал"
                    echo "  Попробуйте вручную: sudo npm install -g @google/gemini-cli"
                }
                if command -v gemini &>/dev/null; then
                    log_ok "Gemini CLI установлен"
                fi
            fi
        else
            log_warn "npm не найден — установите Node.js сначала (см. выше)"
        fi

        # Auth reminder (manual step)
        if command -v gemini &>/dev/null; then
            echo ""
            log_info "Gemini CLI установлен, но требует ручной авторизации."
            echo "  Запустите в терминале:  gemini"
            echo "  Следуйте инструкциям для логина в Google аккаунт."
        fi
    fi

    # ── ngrok ──
    echo ""
    echo -e "${BOLD}1.5 ngrok (туннель для dev-режима)${NC}"
    echo ""

    if command -v ngrok &>/dev/null; then
        log_ok "ngrok уже установлен"
    else
        echo "  ngrok создаёт публичный HTTPS-туннель к вашему localhost."
        echo "  Нужен для тестирования бота без домена."
        echo ""
        if prompt_yn "Установить ngrok?"; then
            if [[ "$OS_KERNEL" == "Darwin" ]]; then
                brew install ngrok 2>/dev/null || true
            else
                if command -v snap &>/dev/null; then
                    sudo snap install ngrok 2>/dev/null || true
                else
                    log_info "Скачиваю ngrok..."
                    local ngrok_arch="amd64"
                    if [[ "${OS_ARCH:-}" == "aarch64" || "${OS_ARCH:-}" == "arm64" ]]; then
                        ngrok_arch="arm64"
                    fi
                    curl -fsSL "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-${ngrok_arch}.tgz" | sudo tar xz -C /usr/local/bin 2>/dev/null || true
                fi
            fi
            if command -v ngrok &>/dev/null; then
                log_ok "ngrok установлен"
            else
                log_warn "Не удалось установить. Скачайте вручную: https://ngrok.com/download"
            fi
        fi
    fi

    # ── Python venv + pip install ──
    echo ""
    echo -e "${BOLD}1.6 Python-зависимости проекта${NC}"
    echo ""

    ensure_venv_ready
    log_ok "Python-зависимости установлены"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_ok "Все программы установлены. Переходим к настройке."
}

# Alias for backward compatibility
setup_dependencies() { setup_install_all; }

# ─── Step 2: .env Interactive ────────────────────────────────

setup_env_interactive() {
    if [[ ! -f .env ]]; then
        cp .env.example .env
        log_ok "Создан .env из шаблона"
    fi

    # Generate webhook secret if placeholder
    local current_secret
    current_secret=$(get_env_var "TELEGRAM_WEBHOOK_SECRET")
    if [[ "$current_secret" == "generate_a_random_string_here" || -z "$current_secret" ]]; then
        local secret
        secret=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
        set_env_var "TELEGRAM_WEBHOOK_SECRET" "$secret"
        log_ok "Сгенерирован TELEGRAM_WEBHOOK_SECRET"
    fi

    # ── Telegram Bot Token ──
    echo ""
    local current_token
    current_token=$(get_env_var "TELEGRAM_BOT_TOKEN")
    if [[ "$current_token" == "your_bot_token_here" || -z "$current_token" ]]; then
        echo -e "${YELLOW}Нужен токен Telegram бота.${NC}"
        echo "  1. Откройте @BotFather в Telegram"
        echo "  2. Отправьте /mybots → выберите @keta_dieta_bot → API Token"
        echo "  3. Скопируйте токен (формат: 123456:ABC-DEF...)"
        echo ""
        prompt_secret "Вставьте TELEGRAM_BOT_TOKEN" BOT_TOKEN
        if [[ -n "$BOT_TOKEN" ]]; then
            set_env_var "TELEGRAM_BOT_TOKEN" "$BOT_TOKEN"
            log_ok "TELEGRAM_BOT_TOKEN сохранён"
        else
            log_warn "Токен не введён — нужно будет добавить позже"
        fi
    else
        log_ok "TELEGRAM_BOT_TOKEN уже настроен"
    fi

    # ── Supabase Service Role Key ──
    echo ""
    local current_supa_key
    current_supa_key=$(get_env_var "SUPABASE_SERVICE_ROLE_KEY")
    if [[ "$current_supa_key" == "your_service_role_key_here" || -z "$current_supa_key" ]]; then
        echo -e "${YELLOW}Нужен Supabase Service Role Key.${NC}"
        echo "  1. Откройте https://supabase.com/dashboard"
        echo "  2. Выберите проект → Settings → API"
        echo "  3. Скопируйте 'service_role' ключ (НЕ 'anon'!)"
        echo -e "  ${RED}⚠ ВАЖНО: нужен именно service_role, а не anon ключ!${NC}"
        echo ""
        prompt_secret "Вставьте SUPABASE_SERVICE_ROLE_KEY" SUPA_KEY
        if [[ -n "$SUPA_KEY" ]]; then
            set_env_var "SUPABASE_SERVICE_ROLE_KEY" "$SUPA_KEY"
            log_ok "SUPABASE_SERVICE_ROLE_KEY сохранён"
        else
            log_warn "Ключ не введён — нужно будет добавить позже"
        fi
    else
        log_ok "SUPABASE_SERVICE_ROLE_KEY уже настроен"
    fi

    # ── Webhook mode ──
    echo ""
    echo -e "Как будет доступен бот из интернета?"
    echo "  1) ngrok  — для разработки/тестирования (бесплатно)"
    echo "  2) domain — для продакшена (нужен домен + SSL)"
    echo ""
    prompt_with_default "Режим вебхука (ngrok/domain)" "ngrok" WH_MODE
    set_env_var "WEBHOOK_MODE" "$WH_MODE"

    if [[ "$WH_MODE" == "domain" ]]; then
        prompt_with_default "Домен с https (например https://bot.example.com)" "" WH_DOMAIN
        if [[ -n "$WH_DOMAIN" ]]; then
            set_env_var "WEBHOOK_DOMAIN" "$WH_DOMAIN"
            log_ok "WEBHOOK_DOMAIN = $WH_DOMAIN"
        fi
    fi

    # ── Webhook port ──
    prompt_with_default "Порт вебхук-сервера" "8080" WH_PORT
    set_env_var "WEBHOOK_PORT" "$WH_PORT"

    # ── LLM CLI command ──
    echo ""
    echo "Какой LLM CLI использовать?"
    echo "  gemini — Google Gemini (по умолчанию)"
    echo "  Можно указать любую другую команду"
    echo ""
    prompt_with_default "Команда LLM CLI" "gemini" LLM_CMD
    set_env_var "LLM_CLI_COMMAND" "$LLM_CMD"

    prompt_with_default "Флаги перед промптом" "-p" LLM_FLAGS
    set_env_var "LLM_CLI_FLAGS" "$LLM_FLAGS"

    # ── Concurrency ──
    prompt_with_default "Макс. параллельных LLM-запросов" "1" LLM_CONC
    set_env_var "MAX_LLM_CONCURRENCY" "$LLM_CONC"

    echo ""
    log_ok "Файл .env настроен"
}

# ─── Step 3: Docker ──────────────────────────────────────────

setup_docker() {
    if ! command -v docker &>/dev/null; then
        log_warn "Docker не установлен"
        log_info "Запустите ./run.sh install и установите Docker"
        return 0
    fi

    if ! docker info &>/dev/null 2>&1; then
        log_warn "Docker не доступен — пропускаю запуск контейнеров"
        if [[ "$OS_KERNEL" == "Darwin" ]]; then
            log_info "Запустите Docker Desktop: open -a Docker"
        else
            log_info "Попробуйте: sudo systemctl start docker"
        fi
        return 0
    fi

    if ! docker_compose up -d; then
        log_err "Не удалось запустить docker compose"
        return 1
    fi

    log_ok "Docker-сервисы запущены (Redis + Postgres)"
    log_info "Жду готовности..."
    sleep 5
}

# ─── Step 4: Migrations ──────────────────────────────────────

setup_migrations() {
    if ! command -v psql &>/dev/null; then
        log_warn "psql не найден — миграции будут применены при первом ./run.sh up"
        return 0
    fi

    cmd_migrate || {
        log_warn "Не удалось применить миграции (Postgres ещё не готов?)"
        log_info "Запустите ./run.sh migrate после старта Docker"
    }
}

# setup_gemini is now integrated into setup_install_all (step 1.4)

# ─── Step 6: Webhook Registration ────────────────────────────

setup_webhook_interactive() {
    # Source env vars
    if [[ -z "${OS_KERNEL:-}" ]]; then
        detect_os
    fi
    load_env

    local token="${TELEGRAM_BOT_TOKEN:-}"
    if [[ -z "$token" || "$token" == "your_bot_token_here" ]]; then
        log_warn "TELEGRAM_BOT_TOKEN не настроен — вебхук нельзя зарегистрировать сейчас"
        log_info "После настройки токена запустите: ./run.sh webhook"
        return 0
    fi

    local mode="${WEBHOOK_MODE:-ngrok}"
    local webhook_url=""

    if [[ "$mode" == "ngrok" ]]; then
        echo "Для dev-режима нужен ngrok (публичный HTTPS → ваш localhost)"
        echo ""

        if command -v ngrok &>/dev/null; then
            log_ok "ngrok найден"
        else
            echo -e "${YELLOW}ngrok не найден.${NC}"
            echo "  Установка:"
            if [[ "$OS_KERNEL" == "Darwin" ]]; then
                echo "    brew install ngrok"
            else
                echo "    snap install ngrok  (или скачайте с https://ngrok.com/download)"
            fi
            echo ""

            if prompt_yn "Попробовать установить ngrok?"; then
                if [[ "$OS_KERNEL" == "Darwin" ]]; then
                    brew install ngrok 2>/dev/null || true
                else
                    sudo snap install ngrok 2>/dev/null || {
                        local ngrok_arch="amd64"
                        if [[ "${OS_ARCH:-}" == "aarch64" || "${OS_ARCH:-}" == "arm64" ]]; then
                            ngrok_arch="arm64"
                        fi
                        curl -fsSL "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-${ngrok_arch}.tgz" | sudo tar xz -C /usr/local/bin 2>/dev/null || true
                    }
                fi
            fi
        fi

        echo ""
        local NGROK_URL=""
        if prompt_yn "Запустить ngrok автоматически и взять URL?" "y"; then
            if start_ngrok_tunnel; then
                NGROK_URL="$NGROK_PUBLIC_URL"
            fi
        fi

        if [[ -z "$NGROK_URL" ]]; then
            echo -e "${CYAN}Для регистрации вебхука через ngrok:${NC}"
            echo "  1. В отдельном терминале запустите: ngrok http ${WEBHOOK_PORT:-8080}"
            echo "  2. Скопируйте HTTPS URL (например: https://abc123.ngrok-free.app)"
            echo ""
            prompt_with_default "Вставьте ngrok HTTPS URL (или оставьте пустым — настроите позже)" "" NGROK_URL
        fi

        if [[ -n "$NGROK_URL" ]]; then
            webhook_url="${NGROK_URL}${WEBHOOK_PATH:-/webhook}"
            set_env_var "WEBHOOK_MODE" "ngrok"
            set_env_var "WEBHOOK_DOMAIN" "$NGROK_URL"
        else
            log_info "Пропущено. Запустите ./run.sh webhook после старта ngrok"
            return 0
        fi
    else
        # Domain mode
        local domain="${WEBHOOK_DOMAIN:-}"
        if [[ -z "$domain" ]]; then
            prompt_with_default "Введите домен (https://...)" "" domain
            if [[ -n "$domain" ]]; then
                set_env_var "WEBHOOK_DOMAIN" "$domain"
            else
                log_warn "Домен не указан"
                return 0
            fi
        fi
        webhook_url="${domain}${WEBHOOK_PATH:-/webhook}"
    fi

    # Register webhook
    if [[ -n "$webhook_url" ]]; then
        echo ""
        log_info "Регистрирую вебхук: $webhook_url"

        local secret="${TELEGRAM_WEBHOOK_SECRET:-}"
        local response
        response=$(curl -sf "https://api.telegram.org/bot${token}/setWebhook" \
            -d "url=${webhook_url}" \
            -d "secret_token=${secret}" \
            -d "allowed_updates=[\"message\",\"callback_query\"]" \
            2>&1) || {
            log_err "Ошибка при регистрации вебхука"
            echo "  $response"
            return 1
        }

        if echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('ok') else 1)" 2>/dev/null; then
            log_ok "Вебхук зарегистрирован: $webhook_url"
        else
            log_err "Telegram отклонил вебхук:"
            echo "  $response"
        fi
    fi
}

# ============================================================
#                    DOCTOR — Health Check
# ============================================================

cmd_doctor() {
    echo "🩺 Health check..."
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
            log_ok "Python $PY_VERSION (>= 3.10 ✓)"
            ((ok++))
        else
            log_err "Python $PY_VERSION — нужен >= 3.10"
            ((fail++))
        fi
    else
        log_err "Python3 не найден"
        ((fail++))
    fi

    # pip
    if python3 -m pip --version &>/dev/null; then
        log_ok "pip"
        ((ok++))
    else
        log_err "pip не найден"
        ((fail++))
    fi

    # Docker
    if command -v docker &>/dev/null; then
        if docker info &>/dev/null; then
            log_ok "Docker работает"
            ((ok++))
        else
            log_warn "Docker установлен, но не запущен"
            ((warn++))
        fi
    else
        log_warn "Docker не установлен"
        ((warn++))
    fi

    # docker compose
    if docker compose version &>/dev/null 2>&1; then
        log_ok "docker compose"
        ((ok++))
    elif command -v docker-compose &>/dev/null; then
        log_ok "docker-compose (v1)"
        ((ok++))
    else
        log_warn "docker compose не найден"
        ((warn++))
    fi

    # Redis
    if command -v redis-cli &>/dev/null && redis-cli ping &>/dev/null 2>&1; then
        log_ok "Redis доступен"
        ((ok++))
    else
        log_warn "Redis не доступен (./run.sh up запустит через Docker)"
        ((warn++))
    fi

    # psql
    if command -v psql &>/dev/null; then
        log_ok "psql"
        ((ok++))
    else
        log_warn "psql не найден (нужен для ./run.sh migrate)"
        ((warn++))
    fi

    # Gemini CLI
    if command -v gemini &>/dev/null; then
        log_ok "Gemini CLI"
        ((ok++))
    else
        log_warn "Gemini CLI не найден (./run.sh setup → шаг 5)"
        ((warn++))
    fi

    # .env file
    if [[ -f .env ]]; then
        log_ok ".env файл существует"
        ((ok++))

        for var in TELEGRAM_BOT_TOKEN SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY; do
            local val
            val=$(get_env_var "$var")
            if [[ -n "$val" && "$val" != your_* ]]; then
                log_ok "  $var ✓"
            else
                log_warn "  $var не настроен"
                ((warn++))
            fi
        done
    else
        log_warn ".env не найден → ./run.sh setup"
        ((warn++))
    fi

    # Ports
    for port in 8080 5432 6379; do
        if lsof -i ":$port" &>/dev/null 2>&1 || ss -tlnp 2>/dev/null | grep -q ":$port "; then
            log_info "Порт $port занят"
        else
            log_ok "Порт $port свободен"
            ((ok++))
        fi
    done

    echo ""
    echo "────────────────────────────────────"
    echo -e "Итого: ${GREEN}$ok ОК${NC}, ${YELLOW}$warn предупр.${NC}, ${RED}$fail ошибок${NC}"

    if [[ $fail -gt 0 ]]; then
        echo ""
        echo -e "${RED}Исправьте ошибки. Или запустите: ./run.sh setup${NC}"
        return 1
    fi
}

# ============================================================
#                    ENV — Interactive .env editor
# ============================================================

cmd_env() {
    if [[ -f .env ]]; then
        echo -e "${BOLD}Текущие настройки .env:${NC}"
        echo ""

        local vars=(
            "TELEGRAM_BOT_TOKEN"
            "SUPABASE_SERVICE_ROLE_KEY"
            "WEBHOOK_MODE"
            "WEBHOOK_DOMAIN"
            "WEBHOOK_PORT"
            "LLM_CLI_COMMAND"
            "MAX_LLM_CONCURRENCY"
            "LOG_LEVEL"
        )

        for var in "${vars[@]}"; do
            local val
            val=$(get_env_var "$var")
            if [[ "$var" == *TOKEN* || "$var" == *KEY* || "$var" == *SECRET* || "$var" == *PASSWORD* ]]; then
                # Mask secrets
                if [[ -n "$val" && "$val" != your_* ]]; then
                    echo -e "  ${GREEN}$var${NC} = ${val:0:8}...***"
                else
                    echo -e "  ${YELLOW}$var${NC} = (не настроен)"
                fi
            else
                echo -e "  ${GREEN}$var${NC} = $val"
            fi
        done

        echo ""
        if prompt_yn "Изменить значения?"; then
            setup_env_interactive
        fi
    else
        setup_env_interactive
    fi
}

# ============================================================
#                    INSTALL — Fix missing deps
# ============================================================

cmd_install() {
    detect_os
    echo ""
    setup_dependencies
}

# ============================================================
#                    WEBHOOK — Register/update
# ============================================================

cmd_webhook() {
    detect_os
    setup_webhook_interactive
}

cmd_tunnel() {
    local action="${1:-status}"
    load_env

    case "$action" in
        up|start)
            start_ngrok_tunnel
            ;;
        down|stop)
            stop_ngrok_tunnel
            ;;
        status)
            if [[ -f "$NGROK_PID_FILE" ]]; then
                local pid
                pid="$(cat "$NGROK_PID_FILE" 2>/dev/null || true)"
                if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
                    local url
                    url="$(get_ngrok_public_url)"
                    if [[ -n "$url" ]]; then
                        log_ok "ngrok запущен (PID: $pid, URL: $url)"
                    else
                        log_warn "ngrok запущен (PID: $pid), но URL не получен"
                    fi
                    return 0
                fi
            fi
            log_warn "ngrok не запущен через run.sh"
            ;;
        url)
            local url
            url="$(get_ngrok_public_url)"
            if [[ -n "$url" ]]; then
                echo "$url"
            else
                log_err "Не удалось получить URL. Убедитесь, что ngrok запущен"
                return 1
            fi
            ;;
        *)
            log_err "Неизвестная команда tunnel: $action"
            echo "Использование: ./run.sh tunnel [up|down|status|url]"
            return 1
            ;;
    esac
}

# ============================================================
#                    UP — Start services
# ============================================================

cmd_up() {
    detect_os
    log_info "Запуск сервисов..."

    # Check .env
    if [[ ! -f .env ]]; then
        log_warn ".env не найден"
        if prompt_yn "Создать и заполнить .env сейчас?" "y"; then
            setup_env_interactive
        else
            log_err "Без .env запуск невозможен"
            return 1
        fi
    fi
    load_env

    # Start Docker services
    if ! command -v docker &>/dev/null; then
        log_err "Docker не найден. Выполните ./run.sh install"
        return 1
    fi
    if ! docker info &>/dev/null 2>&1; then
        log_err "Docker установлен, но не запущен"
        return 1
    fi
    if ! docker_compose up -d; then
        log_err "docker compose не найден или не удалось запустить сервисы"
        return 1
    fi
    log_ok "Docker-сервисы запущены (Redis + Postgres)"

    # Wait
    log_info "Жду готовности..."
    sleep 5

    # Apply local migrations
    cmd_migrate

    # Ensure venv + deps
    ensure_venv_ready

    # Start webhook and worker as managed background processes.
    start_managed_process "webhook" ".webhook.pid" "$WEBHOOK_LOG_FILE" python3 run_webhook.py
    start_managed_process "worker" ".worker.pid" "$WORKER_LOG_FILE" python3 run_worker.py

    echo ""
    log_ok "Все сервисы работают"
    log_info "Логи приложения: ./run.sh logs app"
    log_info "Логи Docker: ./run.sh logs docker"
    log_info "Остановить: ./run.sh down"
}

# ============================================================
#                    DOWN — Stop services
# ============================================================

cmd_down() {
    log_info "Останавливаю сервисы..."

    stop_pidfile_process ".webhook.pid" "webhook"
    stop_pidfile_process ".worker.pid" "worker"
    stop_ngrok_tunnel

    if docker_compose down; then
        log_ok "Docker-сервисы остановлены"
    else
        log_warn "docker compose недоступен — пропускаю остановку контейнеров"
    fi
    log_ok "Сервисы остановлены"
}

# ============================================================
#                    LOGS — Tail
# ============================================================

cmd_logs() {
    local mode="${1:-app}"

    case "$mode" in
        app)
            mkdir -p "$APP_LOG_DIR"
            touch "$WEBHOOK_LOG_FILE" "$WORKER_LOG_FILE" "$NGROK_LOG_FILE"
            echo "Логи приложения (Ctrl+C для выхода)..."
            echo "  - $WEBHOOK_LOG_FILE"
            echo "  - $WORKER_LOG_FILE"
            echo "  - $NGROK_LOG_FILE"
            tail -n 50 -f "$WEBHOOK_LOG_FILE" "$WORKER_LOG_FILE" "$NGROK_LOG_FILE"
            ;;
        docker)
            echo "Логи Docker (Ctrl+C для выхода)..."
            docker_compose logs -f --tail=50
            ;;
        all)
            echo "Сначала покажу логи приложения, затем можно открыть Docker логи отдельной командой."
            cmd_logs app
            ;;
        *)
            log_err "Неизвестный режим логов: $mode"
            echo "Использование: ./run.sh logs [app|docker|all]"
            return 1
            ;;
    esac
}

# ============================================================
#                    MIGRATE — Apply local Postgres migrations
# ============================================================

cmd_migrate() {
    detect_os
    log_info "Применяю миграции локального Postgres..."

    if ! command -v psql &>/dev/null; then
        log_warn "psql не найден"
        if prompt_yn "Установить PostgreSQL client сейчас?" "y"; then
            if [[ "$OS_KERNEL" == "Darwin" ]]; then
                install_package libpq "PostgreSQL client" || true
            else
                install_package postgresql-client "PostgreSQL client" || true
            fi
        fi
    fi
    if ! command -v psql &>/dev/null; then
        log_err "psql по-прежнему не найден, миграции не выполнены"
        return 1
    fi
    load_env

    local PG_HOST="${POSTGRES_HOST:-localhost}"
    local PG_PORT="${POSTGRES_PORT:-5432}"
    local PG_DB="${POSTGRES_DB:-keto_bot}"
    local PG_USER="${POSTGRES_USER:-keto_bot}"

    export PGPASSWORD="${POSTGRES_PASSWORD:-change_me_in_production}"

    # Wait for Postgres
    local retries=30
    while ! psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -c "SELECT 1" &>/dev/null; do
        ((retries--))
        if [[ $retries -le 0 ]]; then
            log_err "Postgres не доступен: $PG_HOST:$PG_PORT"
            unset PGPASSWORD
            return 1
        fi
        sleep 1
    done

    mapfile -t migrations_to_apply < <(ls migrations/00[2-5]_*.sql 2>/dev/null | sort)
    if [[ "${#migrations_to_apply[@]}" -eq 0 ]]; then
        log_warn "Локальные миграции не найдены (ожидались migrations/002..005_*.sql)"
        unset PGPASSWORD
        return 0
    fi

    for migration in "${migrations_to_apply[@]}"; do
        log_info "Применяю: $migration"
        if ! psql -v ON_ERROR_STOP=1 -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -f "$migration"; then
            log_err "Ошибка в миграции: $migration"
            unset PGPASSWORD
            return 1
        fi
        log_ok "$(basename "$migration")"
    done

    unset PGPASSWORD
    log_ok "Миграции применены"
}

# ============================================================
#                    TEST
# ============================================================

cmd_test() {
    local test_type="${1:-unit}"
    load_env

    if [[ -d ".venv" ]]; then
        # shellcheck source=/dev/null
        source .venv/bin/activate
    fi

    case "$test_type" in
        unit)
            log_info "Юнит-тесты..."
            python3 -m pytest tests/unit/ -v --tb=short
            ;;
        integration)
            log_info "Интеграционные тесты (нужен Docker)..."
            python3 -m pytest tests/integration/ -v --tb=short
            ;;
        e2e)
            log_info "E2e тесты..."
            python3 -m pytest tests/e2e/ -v --tb=short
            ;;
        smoke)
            log_info "Smoke-тесты..."
            cmd_doctor
            if curl -sf http://localhost:${WEBHOOK_PORT:-8080}/health &>/dev/null; then
                log_ok "Webhook health check пройден"
            else
                log_err "Webhook не отвечает"
                return 1
            fi
            ;;
        all)
            cmd_test unit
            cmd_test integration
            ;;
        *)
            log_err "Неизвестный тип: $test_type"
            echo "Использование: ./run.sh test [unit|integration|e2e|smoke|all]"
            return 1
            ;;
    esac
}

# ============================================================
#                    SEED / TAGIFY (future)
# ============================================================

cmd_seed() {
    log_info "Seed данные: рецепты уже в Supabase."
    log_info "Для тестовых данных: pytest tests/integration/conftest.py"
}

cmd_tagify() {
    log_info "Recipe enrichment (tagify) ещё не реализован."
    log_info "Будет заполнять recipe_tags таблицу."
}

# ============================================================
#                    MAIN
# ============================================================

cmd="${1:-help}"

case "$cmd" in
    setup)   cmd_setup ;;
    doctor)  cmd_doctor ;;
    env)     cmd_env ;;
    install) cmd_install ;;
    up)      cmd_up ;;
    down)    cmd_down ;;
    webhook) cmd_webhook ;;
    tunnel)  cmd_tunnel "${2:-status}" ;;
    logs)    cmd_logs "${2:-app}" ;;
    migrate) cmd_migrate ;;
    test)    cmd_test "${2:-unit}" ;;
    seed)    cmd_seed ;;
    tagify)  cmd_tagify ;;
    help|*)
        echo "🥑 КетоБот — run.sh"
        echo ""
        echo -e "Использование: ${BOLD}./run.sh <команда>${NC}"
        echo ""
        echo -e "${BOLD}Первый запуск:${NC}"
        echo "  setup      Интерактивный мастер настройки (начните сюда!)"
        echo ""
        echo -e "${BOLD}Управление:${NC}"
        echo "  up         Запустить все сервисы"
        echo "  down       Остановить все сервисы"
        echo "  tunnel     Управление ngrok [up|down|status|url]"
        echo "  logs       Логи [app|docker|all]"
        echo ""
        echo -e "${BOLD}Настройка:${NC}"
        echo "  env        Показать / изменить .env"
        echo "  install    Установить недостающие зависимости"
        echo "  webhook    Зарегистрировать Telegram вебхук"
        echo "  migrate    Применить миграции Postgres"
        echo ""
        echo -e "${BOLD}Тестирование:${NC}"
        echo "  doctor     Проверить состояние системы"
        echo "  test       Запустить тесты [unit|integration|e2e|smoke|all]"
        echo ""
        echo -e "${BOLD}Данные:${NC}"
        echo "  seed       Seed тестовых данных"
        echo "  tagify     Обогащение тегов рецептов"
        echo ""
        ;;
esac
