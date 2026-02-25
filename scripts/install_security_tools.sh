#!/usr/bin/env bash
set -euo pipefail

PROFILE="standard"
STRICT_MODE=0
SKIP_UPDATE=0
NON_INTERACTIVE=0
NO_BROWSER=0
LIST_CATEGORIES=0
DRY_RUN=0
APT_RETRIES="${APT_RETRIES:-3}"
CATEGORY_INPUTS=()

log() {
  printf '[install-tools] %s\n' "$*"
}

warn() {
  printf '[install-tools][warn] %s\n' "$*" >&2
}

die() {
  printf '[install-tools][error] %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  scripts/install_security_tools.sh [options]

Options:
  --profile <name>     Install profile: minimal|standard|full|network|web|auth|binary|forensics|cloud|osint|browser|experimental
  --category <names>   Install by category (can repeat, supports comma-separated values)
  --list-categories    Show all categories and exit
  --dry-run            Print resolved package list and exit
  --strict             Exit non-zero when any package fails or is unavailable
  --skip-update        Skip apt repository update
  --non-interactive    Set DEBIAN_FRONTEND=noninteractive
  --no-browser         Skip browser dependencies (chromium, chromium-driver)
  -h, --help           Show this help message

Examples:
  scripts/install_security_tools.sh --profile standard
  scripts/install_security_tools.sh --category web,forensics --strict
  scripts/install_security_tools.sh --category network --category web --non-interactive
  scripts/install_security_tools.sh --category web,forensics --dry-run
  scripts/install_security_tools.sh --profile full --strict --non-interactive
USAGE
}

list_categories() {
  cat <<'CATEGORIES'
Available categories:
  network       Network discovery and reconnaissance tools
  web           Web security testing tools
  auth          Authentication and password auditing tools
  binary        Binary analysis and reverse-engineering tools
  forensics     Digital forensics and artifact extraction tools
  cloud         Cloud and Kubernetes baseline tools
  osint         OSINT-friendly reconnaissance tools
  browser       Browser runtime dependencies
  experimental  Advanced tools that may require extra repositories
CATEGORIES
}

trim_whitespace() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

join_by_comma() {
  local IFS=','
  printf '%s' "$*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || die "--profile requires a value"
      PROFILE="$2"
      shift 2
      ;;
    --category)
      [[ $# -ge 2 ]] || die "--category requires a value"
      CATEGORY_INPUTS+=("$2")
      shift 2
      ;;
    --list-categories)
      LIST_CATEGORIES=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --strict)
      STRICT_MODE=1
      shift
      ;;
    --skip-update)
      SKIP_UPDATE=1
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    --no-browser)
      NO_BROWSER=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

run_apt_get() {
  local attempt=1
  local max_attempts="${APT_RETRIES}"

  if [[ "$max_attempts" -lt 1 ]]; then
    max_attempts=1
  fi

  while [[ "$attempt" -le "$max_attempts" ]]; do
    if [[ -n "$SUDO_CMD" ]]; then
      if "$SUDO_CMD" apt-get "$@"; then
        return 0
      fi
    else
      if apt-get "$@"; then
        return 0
      fi
    fi

    if [[ "$attempt" -lt "$max_attempts" ]]; then
      warn "apt-get $* failed (attempt ${attempt}/${max_attempts}), retrying..."
      sleep $((attempt * 2))
    fi
    attempt=$((attempt + 1))
  done

  return 1
}

NETWORK_TOOLS=(
  nmap masscan amass subfinder fierce dnsenum theharvester
  responder enum4linux enum4linux-ng netexec
)
WEB_TOOLS=(
  gobuster feroxbuster ffuf dirb dirsearch nikto sqlmap wpscan
  arjun wafw00f
)
AUTH_TOOLS=(
  hydra john hashcat medusa patator evil-winrm hash-identifier
)
BINARY_TOOLS=(
  gdb radare2 binwalk checksec binutils
)
FORENSICS_TOOLS=(
  volatility3 foremost steghide exiftool sleuthkit testdisk
)
CLOUD_TOOLS=(
  awscli kubectl
)
OSINT_TOOLS=(
  amass subfinder theharvester
)
BROWSER_TOOLS=(
  chromium chromium-driver
)
EXPERIMENTAL_TOOLS=(
  checkov dalfox docker-bench-security falco httpx kube-bench kube-hunter
  recon-ng rustscan sherlock spiderfoot terrascan trivy
)

profile_categories() {
  case "$1" in
    minimal)
      printf '%s\n' network web auth
      ;;
    standard)
      printf '%s\n' network web auth binary forensics
      ;;
    full)
      printf '%s\n' network web auth binary forensics cloud osint browser
      ;;
    network|web|auth|binary|forensics|cloud|osint|browser|experimental)
      printf '%s\n' "$1"
      ;;
    *)
      die "Unsupported profile: $1"
      ;;
  esac
}

category_packages() {
  case "$1" in
    network)
      printf '%s\n' "${NETWORK_TOOLS[@]}"
      ;;
    web)
      printf '%s\n' "${WEB_TOOLS[@]}"
      ;;
    auth)
      printf '%s\n' "${AUTH_TOOLS[@]}"
      ;;
    binary)
      printf '%s\n' "${BINARY_TOOLS[@]}"
      ;;
    forensics)
      printf '%s\n' "${FORENSICS_TOOLS[@]}"
      ;;
    cloud)
      printf '%s\n' "${CLOUD_TOOLS[@]}"
      ;;
    osint)
      printf '%s\n' "${OSINT_TOOLS[@]}"
      ;;
    browser)
      printf '%s\n' "${BROWSER_TOOLS[@]}"
      ;;
    experimental)
      printf '%s\n' "${EXPERIMENTAL_TOOLS[@]}"
      ;;
    *)
      die "Unsupported category: $1"
      ;;
  esac
}

resolve_pkg_candidates() {
  case "$1" in
    volatility3)
      printf '%s\n' "python3-volatility3 volatility3"
      ;;
    exiftool)
      printf '%s\n' "libimage-exiftool-perl exiftool"
      ;;
    kubectl)
      printf '%s\n' "kubernetes-client kubectl"
      ;;
    chromium-driver)
      printf '%s\n' "chromium-driver chromium-chromedriver"
      ;;
    hash-identifier)
      printf '%s\n' "hash-identifier hashid"
      ;;
    *)
      printf '%s\n' "$1"
      ;;
  esac
}

parse_categories() {
  local raw_input
  local token
  local normalized
  local -a split_values=()
  declare -A seen=()

  for raw_input in "$@"; do
    IFS=',' read -r -a split_values <<<"$raw_input"
    for token in "${split_values[@]}"; do
      normalized="$(trim_whitespace "$token")"
      [[ -z "$normalized" ]] && continue
      case "$normalized" in
        network|web|auth|binary|forensics|cloud|osint|browser|experimental)
          if [[ -z "${seen[$normalized]:-}" ]]; then
            seen["$normalized"]=1
            printf '%s\n' "$normalized"
          fi
          ;;
        *)
          die "Unsupported category: $normalized"
          ;;
      esac
    done
  done
}

collect_packages_from_categories() {
  local category
  for category in "$@"; do
    category_packages "$category"
  done
}

if [[ "$LIST_CATEGORIES" -eq 1 ]]; then
  list_categories
  exit 0
fi

SELECTED_CATEGORIES=()
SOURCE_DESCRIPTION=""
if [[ "${#CATEGORY_INPUTS[@]}" -gt 0 ]]; then
  readarray -t SELECTED_CATEGORIES < <(parse_categories "${CATEGORY_INPUTS[@]}")
  SOURCE_DESCRIPTION="categories: $(join_by_comma "${SELECTED_CATEGORIES[@]}")"
else
  readarray -t SELECTED_CATEGORIES < <(profile_categories "$PROFILE")
  SOURCE_DESCRIPTION="profile: ${PROFILE} (categories: $(join_by_comma "${SELECTED_CATEGORIES[@]}"))"
fi

if [[ "${#SELECTED_CATEGORIES[@]}" -eq 0 ]]; then
  die "No categories selected"
fi

readarray -t PACKAGES < <(collect_packages_from_categories "${SELECTED_CATEGORIES[@]}" | awk 'NF {print $1}' | sort -u)

if [[ "$NO_BROWSER" -eq 1 ]]; then
  FILTERED=()
  for pkg in "${PACKAGES[@]}"; do
    if [[ "$pkg" != "chromium" && "$pkg" != "chromium-driver" ]]; then
      FILTERED+=("$pkg")
    fi
  done
  PACKAGES=("${FILTERED[@]}")
fi

if [[ "${#PACKAGES[@]}" -eq 0 ]]; then
  die "No packages selected"
fi

log "Selection: $SOURCE_DESCRIPTION"
log "Total packages selected: ${#PACKAGES[@]}"

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "Dry-run mode enabled. Resolved packages:"
  printf '%s\n' "${PACKAGES[@]}"
  exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
  die "This script currently supports apt-based systems only"
fi

SUDO_CMD=""
if [[ "$(id -u)" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO_CMD="sudo"
  else
    die "Please run as root or install sudo"
  fi
fi

if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
  export DEBIAN_FRONTEND=noninteractive
fi

if [[ "$SKIP_UPDATE" -eq 0 ]]; then
  log "Updating apt repositories"
  run_apt_get update
else
  log "Skipping apt update"
fi

INSTALLED=()
ALREADY_INSTALLED=()
FAILED=()
UNAVAILABLE=()

install_pkg() {
  local tool="$1"
  local selected_pkg=""
  local candidate=""
  local display_name=""
  local -a candidates=()

  read -r -a candidates <<<"$(resolve_pkg_candidates "$tool")"

  for candidate in "${candidates[@]}"; do
    if dpkg-query -W -f='${Status}' "$candidate" 2>/dev/null | grep -q "install ok installed"; then
      display_name="$tool"
      if [[ "$candidate" != "$tool" ]]; then
        display_name="${tool}(${candidate})"
      fi
      ALREADY_INSTALLED+=("$display_name")
      return 0
    fi
  done

  for candidate in "${candidates[@]}"; do
    if apt-cache show "$candidate" >/dev/null 2>&1; then
      selected_pkg="$candidate"
      break
    fi
  done

  if [[ -z "$selected_pkg" ]]; then
    UNAVAILABLE+=("$tool")
    warn "Package not found in repositories: $tool (candidates: ${candidates[*]})"
    return 1
  fi

  if [[ "$selected_pkg" != "$tool" ]]; then
    log "Using package '${selected_pkg}' for tool '${tool}'"
  fi

  if run_apt_get install -y --no-install-recommends "$selected_pkg"; then
    display_name="$tool"
    if [[ "$selected_pkg" != "$tool" ]]; then
      display_name="${tool}(${selected_pkg})"
    fi
    INSTALLED+=("$display_name")
    return 0
  fi

  FAILED+=("$tool")
  warn "Failed to install package: $tool (selected: $selected_pkg)"
  return 1
}

for pkg in "${PACKAGES[@]}"; do
  install_pkg "$pkg" || true

done

log "Installation summary"
log "  Installed: ${#INSTALLED[@]}"
log "  Already installed: ${#ALREADY_INSTALLED[@]}"
log "  Failed: ${#FAILED[@]}"
log "  Unavailable: ${#UNAVAILABLE[@]}"

if [[ "${#FAILED[@]}" -gt 0 ]]; then
  warn "Failed packages: ${FAILED[*]}"
fi
if [[ "${#UNAVAILABLE[@]}" -gt 0 ]]; then
  warn "Unavailable packages: ${UNAVAILABLE[*]}"
fi

if [[ "$STRICT_MODE" -eq 1 && ( "${#FAILED[@]}" -gt 0 || "${#UNAVAILABLE[@]}" -gt 0 ) ]]; then
  die "Strict mode enabled and not all packages installed"
fi

log "Done"
