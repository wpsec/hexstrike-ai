#!/usr/bin/env bash
set -u

PROFILE="standard"
STRICT_MODE=0
SKIP_UPDATE=0
NON_INTERACTIVE=0
NO_BROWSER=0

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
  --profile <name>     Install profile: minimal|standard|full|network|web|auth|binary|forensics|cloud|osint|browser
  --strict             Exit non-zero when any package fails or is unavailable
  --skip-update        Skip apt repository update
  --non-interactive    Set DEBIAN_FRONTEND=noninteractive
  --no-browser         Skip browser dependencies (chromium, chromium-driver)
  -h, --help           Show this help message

Examples:
  scripts/install_security_tools.sh --profile standard
  scripts/install_security_tools.sh --profile full --strict --non-interactive
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || die "--profile requires a value"
      PROFILE="$2"
      shift 2
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

NETWORK_TOOLS=(
  nmap masscan rustscan amass subfinder fierce dnsenum theharvester
  responder enum4linux enum4linux-ng netexec
)
WEB_TOOLS=(
  gobuster feroxbuster ffuf dirb dirsearch nikto sqlmap wpscan
  arjun dalfox wafw00f httpx
)
AUTH_TOOLS=(
  hydra john hashcat medusa patator evil-winrm hash-identifier
)
BINARY_TOOLS=(
  gdb radare2 binwalk checksec strings binutils
)
FORENSICS_TOOLS=(
  volatility3 foremost steghide exiftool sleuthkit testdisk
)
CLOUD_TOOLS=(
  trivy kube-hunter kube-bench docker-bench-security checkov terrascan falco
)
OSINT_TOOLS=(
  recon-ng spiderfoot sherlock
)
BROWSER_TOOLS=(
  chromium chromium-driver
)

select_packages() {
  case "$PROFILE" in
    minimal)
      printf '%s\n' "${NETWORK_TOOLS[@]}" "${WEB_TOOLS[@]}" "${AUTH_TOOLS[@]}"
      ;;
    standard)
      printf '%s\n' \
        "${NETWORK_TOOLS[@]}" "${WEB_TOOLS[@]}" "${AUTH_TOOLS[@]}" \
        "${BINARY_TOOLS[@]}" "${FORENSICS_TOOLS[@]}"
      ;;
    full)
      printf '%s\n' \
        "${NETWORK_TOOLS[@]}" "${WEB_TOOLS[@]}" "${AUTH_TOOLS[@]}" \
        "${BINARY_TOOLS[@]}" "${FORENSICS_TOOLS[@]}" "${CLOUD_TOOLS[@]}" \
        "${OSINT_TOOLS[@]}" "${BROWSER_TOOLS[@]}"
      ;;
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
    *)
      die "Unsupported profile: $PROFILE"
      ;;
  esac
}

readarray -t PACKAGES < <(select_packages | awk 'NF {print $1}' | sort -u)

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
  die "No packages selected for profile: $PROFILE"
fi

log "Profile: $PROFILE"
log "Total packages selected: ${#PACKAGES[@]}"

if [[ "$SKIP_UPDATE" -eq 0 ]]; then
  log "Updating apt repositories"
  $SUDO_CMD apt-get update
else
  log "Skipping apt update"
fi

INSTALLED=()
ALREADY_INSTALLED=()
FAILED=()
UNAVAILABLE=()

install_pkg() {
  local pkg="$1"

  if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
    ALREADY_INSTALLED+=("$pkg")
    return 0
  fi

  if ! apt-cache show "$pkg" >/dev/null 2>&1; then
    UNAVAILABLE+=("$pkg")
    warn "Package not found in repositories: $pkg"
    return 1
  fi

  if $SUDO_CMD apt-get install -y --no-install-recommends "$pkg"; then
    INSTALLED+=("$pkg")
    return 0
  fi

  FAILED+=("$pkg")
  warn "Failed to install package: $pkg"
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
