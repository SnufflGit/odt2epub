#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"
CHECK_ONLY=false

usage() {
    cat <<'EOF'
Usage: install_dependencies.sh [--check-only]

Check and install the converter's system and Python dependencies on
Debian/Ubuntu. Python packages are installed in the project's .venv.

Options:
  --check-only  Report missing dependencies without installing anything
  -h, --help    Show this help
EOF
}

for arg in "$@"; do
    case "$arg" in
        --check-only) CHECK_ONLY=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg-query >/dev/null 2>&1; then
    echo "This installer supports Debian/Ubuntu systems with apt-get and dpkg-query." >&2
    exit 1
fi

missing_packages=()
add_package() {
    local package="$1"
    local existing
    for existing in "${missing_packages[@]:-}"; do
        [[ "$existing" == "$package" ]] && return
    done
    missing_packages+=("$package")
}

check_command() {
    local command_name="$1"
    local package="$2"
    if command -v "$command_name" >/dev/null 2>&1; then
        printf 'Found: %s\n' "$command_name"
    else
        printf 'Missing: %s (package: %s)\n' "$command_name" "$package"
        add_package "$package"
    fi
}

check_command python3 python3
check_command pandoc pandoc
check_command xelatex texlive-xetex
check_command pdfinfo poppler-utils
check_command pdftotext poppler-utils
check_command pdftoppm poppler-utils
check_command qpdf qpdf
check_command ffmpeg ffmpeg

for package in python3-venv texlive-latex-recommended fonts-dejavu-core; do
    if [[ "$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)" == "installed" ]]; then
        printf 'Found package: %s\n' "$package"
    else
        printf 'Missing package: %s\n' "$package"
        add_package "$package"
    fi
done

if [[ -x "${VENV_DIR}/bin/python" ]]; then
    if "${VENV_DIR}/bin/python" -c 'import PIL' >/dev/null 2>&1; then
        echo "Found Python package: Pillow (.venv)"
    else
        echo "Missing Python package: Pillow (.venv)"
    fi
else
    echo "Missing Python environment: ${VENV_DIR}"
fi

if "$CHECK_ONLY"; then
    if ((${#missing_packages[@]} > 0)) || [[ ! -x "${VENV_DIR}/bin/python" ]] || \
       ! "${VENV_DIR}/bin/python" -c 'import PIL' >/dev/null 2>&1; then
        echo "Some dependencies are missing. Run: bash install_dependencies.sh"
        exit 1
    fi
    echo "All dependencies are installed."
    exit 0
fi

if ((${#missing_packages[@]} > 0)); then
    if ((EUID == 0)); then
        apt_command=(apt-get)
    elif command -v sudo >/dev/null 2>&1; then
        apt_command=(sudo apt-get)
    else
        echo "Missing system packages, and neither root nor sudo is available." >&2
        exit 1
    fi

    "${apt_command[@]}" update
    "${apt_command[@]}" install -y -- "${missing_packages[@]}"
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is still unavailable after installing system packages." >&2
    exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    python3 -m venv "$VENV_DIR"
fi

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    echo "Requirements file not found: ${REQUIREMENTS_FILE}" >&2
    exit 1
fi

"${VENV_DIR}/bin/python" -m pip install -r "$REQUIREMENTS_FILE"
echo "Dependency setup is complete."
