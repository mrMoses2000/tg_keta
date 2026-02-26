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
    echo "Этот мастер поможет настроить всё необходимое:"
    echo "  1. Проверка и установка зависимостей"
    echo "  2. Настройка .env (токены, ключи)"
    echo "  3. Запуск Docker-сервисов (Redis + Postgres)"
    echo "  4. Применение миграций БД"
    echo "  5. Настройка и установка Gemini CLI"
    echo "  6. Регистрация Telegram вебхука"
    echo ""

    detect_os

    if ! prompt_yn "Начать настройку?" "y"; then
        echo "Отменено."
        return 0
    fi

    # ── Step 1: Dependencies ──
    log_step "Шаг 1/6: Проверка зависимостей"
    setup_dependencies

    # ── Step 2: .env Configuration ──
    log_step "Шаг 2/6: Настройка .env"
    setup_env_interactive

    # ── Step 3: Docker Services ──
    log_step "Шаг 3/6: Docker-сервисы (Redis + Postgres)"
    setup_docker

    # ── Step 4: Migrations ──
    log_step "Шаг 4/6: Миграции базы данных"
    setup_migrations

    # ── Step 5: Gemini CLI ──
    log_step "Шаг 5/6: Gemini CLI"
    setup_gemini

    # ── Step 6: Webhook ──
    log_step "Шаг 6/6: Telegram вебхук"
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

# ─── Step 1: Dependencies ────────────────────────────────────

setup_dependencies() {
    local missing=()

    # Python 3.10+
    if command -v python3 &>/dev/null; then
        PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [[ "$PY_MINOR" -ge 10 ]]; then
            log_ok "Python $PY_VERSION"
        else
            log_err "Python $PY_VERSION — нужен >= 3.10"
            missing+=("python3")
        fi
    else
        log_err "Python3 не найден"
        missing+=("python3")
    fi

    # pip
    if python3 -m pip --version &>/dev/null 2>&1; then
        log_ok "pip"
    else
        log_warn "pip не найден"
        missing+=("python3-pip")
    fi

    # Docker
    if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
        log_ok "Docker"
    else
        log_warn "Docker не найден / не запущен"
        missing+=("docker")
    fi

    # psql
    if command -v psql &>/dev/null; then
        log_ok "psql"
    else
        log_warn "psql не найден (нужен для миграций)"
        missing+=("postgresql-client")
    fi

    # Node.js (for Gemini CLI)
    if command -v node &>/dev/null; then
        log_ok "Node.js $(node --version)"
    else
        log_warn "Node.js не найден (нужен для Gemini CLI)"
        missing+=("nodejs")
    fi

    # Install missing
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo ""
        log_warn "Не хватает: ${missing[*]}"
        if prompt_yn "Попробовать установить автоматически?"; then
            for pkg in "${missing[@]}"; do
                case "$pkg" in
                    docker)
                        echo ""
                        log_info "Docker лучше установить вручную:"
                        if [[ "$OS_KERNEL" == "Darwin" ]]; then
                            echo "  → https://docs.docker.com/desktop/install/mac-install/"
                            echo "  или: brew install --cask docker"
                        else
                            echo "  → https://docs.docker.com/engine/install/ubuntu/"
                            echo "  или: curl -fsSL https://get.docker.com | sh"
                            if prompt_yn "Выполнить curl -fsSL https://get.docker.com | sh ?"; then
                                curl -fsSL https://get.docker.com | sh
                                sudo usermod -aG docker "$USER" 2>/dev/null || true
                                log_ok "Docker установлен (может потребоваться перелогиниться)"
                            fi
                        fi
                        ;;
                    nodejs)
                        if [[ "$OS_KERNEL" == "Darwin" ]]; then
                            install_package node "Node.js" || true
                        else
                            log_info "Устанавливаю Node.js 20 LTS..."
                            if command -v apt-get &>/dev/null; then
                                curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - 2>/dev/null
                                sudo apt-get install -y -qq nodejs 2>/dev/null || true
                            fi
                        fi
                        ;;
                    *)
                        install_package "$pkg" "$pkg" || true
                        ;;
                esac
            done
        fi
    else
        log_ok "Все зависимости на месте"
    fi

    # Python venv + pip install
    echo ""
    if [[ ! -d ".venv" ]]; then
        log_info "Создаю Python вirtualenv..."
        python3 -m venv .venv
    fi
    # shellcheck source=/dev/null
    source .venv/bin/activate
    log_info "Устанавливаю Python-зависимости..."
    pip install -q -r requirements.txt
    log_ok "Python-зависимости установлены"
}

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

    echo ""
    log_ok "Файл .env настроен"
}

# ─── Step 3: Docker ──────────────────────────────────────────

setup_docker() {
    if ! command -v docker &>/dev/null || ! docker info &>/dev/null 2>&1; then
        log_warn "Docker не доступен — пропускаю запуск контейнеров"
        log_info "Установите Docker и запустите ./run.sh setup заново"
        return 0
    fi

    if docker compose version &>/dev/null; then
        docker compose up -d
    elif command -v docker-compose &>/dev/null; then
        docker-compose up -d
    fi

    log_ok "Docker-сервисы запущены (Redis + Postgres)"
    log_info "Жду готовности..."
    sleep 3
}

# ─── Step 4: Migrations ──────────────────────────────────────

setup_migrations() {
    if ! command -v psql &>/dev/null; then
        log_warn "psql не найден — миграции будут применены при первом ./run.sh up"
        return 0
    fi

    cmd_migrate 2>/dev/null || {
        log_warn "Не удалось применить миграции (Postgres ещё не готов?)"
        log_info "Запустите ./run.sh migrate после старта Docker"
    }
}

# ─── Step 5: Gemini CLI ─────────────────────────────────────

setup_gemini() {
    if command -v gemini &>/dev/null; then
        log_ok "Gemini CLI уже установлен"
        gemini --version 2>/dev/null || true
        return 0
    fi

    echo -e "${YELLOW}Gemini CLI не найден в PATH.${NC}"
    echo ""
    echo "Gemini CLI — это инструмент для вызова Google Gemini из командной строки."
    echo "Бот использует его для генерации ответов."
    echo ""

    if ! command -v npm &>/dev/null && ! command -v npx &>/dev/null; then
        log_warn "npm/npx не найден — установите Node.js сначала"
        echo "  macOS:  brew install node"
        echo "  Ubuntu: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs"
        return 0
    fi

    if prompt_yn "Установить Gemini CLI через npm?" "y"; then
        log_info "Устанавливаю @anthropic-ai/claude-code... (это может занять минуту)"
        npm install -g @google/gemini-cli 2>/dev/null || {
            log_warn "npm install не сработал, попробуйте вручную:"
            echo "  npm install -g @google/gemini-cli"
        }

        if command -v gemini &>/dev/null; then
            log_ok "Gemini CLI установлен"
            echo ""
            echo -e "${YELLOW}Нужна авторизация Gemini:${NC}"
            echo "  Запустите: gemini"
            echo "  Следуйте инструкциям для логина в Google"
            echo ""
            if prompt_yn "Запустить gemini сейчас для авторизации?"; then
                gemini || true
            fi
        fi
    else
        log_info "Пропущено. Установите позже:"
        echo "  npm install -g @google/gemini-cli"
        echo "  gemini  # для авторизации"
    fi

    # Ask which LLM to use
    echo ""
    echo "Какой LLM CLI использовать?"
    echo "  1) gemini  — Google Gemini (по умолчанию)"
    echo "  2) другой  — указать вручную"
    echo ""
    prompt_with_default "Команда LLM CLI" "gemini" LLM_CMD
    set_env_var "LLM_CLI_COMMAND" "$LLM_CMD"

    prompt_with_default "Флаги LLM CLI (перед промптом)" "-p" LLM_FLAGS
    set_env_var "LLM_CLI_FLAGS" "$LLM_FLAGS"
}

# ─── Step 6: Webhook Registration ────────────────────────────

setup_webhook_interactive() {
    # Source env vars
    if [[ -f .env ]]; then
        set -a; source .env 2>/dev/null; set +a
    fi

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
                        curl -fsSL https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz | sudo tar xz -C /usr/local/bin 2>/dev/null || true
                    }
                fi
            fi
        fi

        echo ""
        echo -e "${CYAN}Для регистрации вебхука через ngrok:${NC}"
        echo "  1. В отдельном терминале запустите: ngrok http ${WEBHOOK_PORT:-8080}"
        echo "  2. Скопируйте HTTPS URL (например: https://abc123.ngrok-free.app)"
        echo ""
        prompt_with_default "Вставьте ngrok HTTPS URL (или оставьте пустым — настроите позже)" "" NGROK_URL

        if [[ -n "$NGROK_URL" ]]; then
            webhook_url="${NGROK_URL}${WEBHOOK_PATH:-/webhook}"
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
    setup_webhook_interactive
}

# ============================================================
#                    UP — Start services
# ============================================================

cmd_up() {
    log_info "Запуск сервисов..."

    # Check .env
    if [[ ! -f .env ]]; then
        log_err ".env не найден. Сначала запустите: ./run.sh setup"
        return 1
    fi

    # Start Docker services
    if docker compose version &>/dev/null 2>&1; then
        docker compose up -d
    elif command -v docker-compose &>/dev/null; then
        docker-compose up -d
    else
        log_err "docker compose не найден"
        return 1
    fi
    log_ok "Docker-сервисы запущены (Redis + Postgres)"

    # Wait
    log_info "Жду готовности..."
    sleep 3

    # Apply local migrations
    cmd_migrate

    # Ensure venv + deps
    if [[ ! -d ".venv" ]]; then
        python3 -m venv .venv
    fi
    # shellcheck source=/dev/null
    source .venv/bin/activate
    pip install -q -r requirements.txt

    # Start webhook and worker
    log_info "Запускаю webhook сервер..."
    python3 run_webhook.py &
    WEBHOOK_PID=$!
    echo "$WEBHOOK_PID" > .webhook.pid
    log_ok "Webhook запущен (PID: $WEBHOOK_PID)"

    log_info "Запускаю worker..."
    python3 run_worker.py &
    WORKER_PID=$!
    echo "$WORKER_PID" > .worker.pid
    log_ok "Worker запущен (PID: $WORKER_PID)"

    echo ""
    log_ok "Все сервисы работают"
    log_info "Логи: ./run.sh logs"
    log_info "Остановить: ./run.sh down"
}

# ============================================================
#                    DOWN — Stop services
# ============================================================

cmd_down() {
    log_info "Останавливаю сервисы..."

    for pidfile in .webhook.pid .worker.pid; do
        if [[ -f "$pidfile" ]]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid"
                log_ok "Остановлен процесс $pid"
            fi
            rm -f "$pidfile"
        fi
    done

    if docker compose version &>/dev/null 2>&1; then
        docker compose down
    elif command -v docker-compose &>/dev/null; then
        docker-compose down
    fi
    log_ok "Сервисы остановлены"
}

# ============================================================
#                    LOGS — Tail
# ============================================================

cmd_logs() {
    echo "Логи Docker (Ctrl+C для выхода)..."
    if docker compose version &>/dev/null 2>&1; then
        docker compose logs -f --tail=50
    elif command -v docker-compose &>/dev/null; then
        docker-compose logs -f --tail=50
    fi
}

# ============================================================
#                    MIGRATE — Apply local Postgres migrations
# ============================================================

cmd_migrate() {
    log_info "Применяю миграции локального Postgres..."

    if [[ -f .env ]]; then
        set -a; source .env 2>/dev/null; set +a
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
            log_err "Postgres не доступен: $PG_HOST:$PG_PORT"
            return 1
        fi
        sleep 1
    done

    for migration in migrations/002_*.sql migrations/003_*.sql migrations/004_*.sql migrations/005_*.sql; do
        if [[ -f "$migration" ]]; then
            log_info "Применяю: $migration"
            psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -f "$migration" 2>&1 | grep -v "^NOTICE" || true
            log_ok "$(basename "$migration")"
        fi
    done

    unset PGPASSWORD
    log_ok "Миграции применены"
}

# ============================================================
#                    TEST
# ============================================================

cmd_test() {
    local test_type="${1:-unit}"

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
    logs)    cmd_logs ;;
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
        echo "  logs       Логи Docker-контейнеров"
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
