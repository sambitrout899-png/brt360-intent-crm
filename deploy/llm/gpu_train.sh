#!/usr/bin/env bash
# BRT 360 - train the telecom CRM model on a rented NVIDIA GPU server (Hostinger, RunPod, E2E, ...).
# Runs on the GPU server, NOT on the CRM server. Normally driven from your PC by train_on_gpu.ps1.
#
#   bash gpu_train.sh setup                         # check GPU/driver, install Python + Unsloth (once, ~10 min)
#   bash gpu_train.sh train data.jsonl [finetune.py options]   # starts in the background, survives SSH disconnects
#   bash gpu_train.sh status                        # progress (log tail, GPU use)
#
# Output in ~/brt360-train/out/: brt360-telecom.gguf + brt360-telecom-holdout.jsonl
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${WORK:-$HOME/brt360-train}"
VENV="$WORK/.venv"
OUT="$WORK/out"
LOG="$WORK/train.log"
SUDO=""; [[ $EUID -ne 0 ]] && SUDO="sudo"

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

check_gpu() {
  if ! command -v nvidia-smi >/dev/null || ! nvidia-smi >/dev/null 2>&1; then
    lspci 2>/dev/null | grep -qi nvidia || die "No NVIDIA GPU on this server. Training needs one (16 GB+ VRAM: T4, L4, A10, RTX 4090, A100...)."
    die "NVIDIA GPU found but no working driver. Install it and reboot, then run setup again:
       $SUDO apt-get update && $SUDO apt-get install -y ubuntu-drivers-common && $SUDO ubuntu-drivers autoinstall && $SUDO reboot"
  fi
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | sed 's/^/GPU: /'
  local vram; vram="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)"
  (( vram >= 15000 )) || die "GPU has ${vram} MiB; at least 16 GB is needed for the 3B model at 8k context."
}

cmd_setup() {
  step "Checking the GPU"
  check_gpu
  step "System packages (Python, compiler for the GGUF export)"
  $SUDO apt-get update -qq
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-dev python3-pip \
    build-essential cmake git curl libcurl4-openssl-dev >/dev/null
  # Loading the model and the GGUF export (merge to 16-bit, then convert) peak above 8 GB of system RAM;
  # small-RAM GPU instances get a swap file instead of being killed halfway through.
  local ram_gb; ram_gb="$(awk '/MemTotal/ {print int($2/1048576)}' /proc/meminfo)"
  # Containers (most hourly GPU instances) may not allow swap - then carry on: the LoRA adapter is saved
  # before the export, so a memory failure there loses no training.
  if (( ram_gb < 30 )) && ! grep -q /swapfile-brt360 /proc/swaps 2>/dev/null; then
    step "Only ${ram_gb} GB RAM - trying to add a 24 GB swap file for the export"
    if $SUDO fallocate -l 24G /swapfile-brt360 && $SUDO chmod 600 /swapfile-brt360 \
       && $SUDO mkswap /swapfile-brt360 >/dev/null && $SUDO swapon /swapfile-brt360 2>/dev/null; then
      echo "Swap added."
    else
      $SUDO rm -f /swapfile-brt360
      echo "Swap is not allowed on this server (container) - continuing without it."
    fi
  fi
  step "Python environment + Unsloth in $VENV (5-10 min)"
  mkdir -p "$WORK" "$OUT"
  [[ -d "$VENV" ]] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip wheel
  "$VENV/bin/pip" install -q unsloth
  "$VENV/bin/python" - <<'EOF'
import torch
assert torch.cuda.is_available(), "PyTorch cannot see the GPU - check the driver (nvidia-smi)"
print(f"PyTorch {torch.__version__} sees {torch.cuda.get_device_name(0)}")
import unsloth  # noqa: F401
print("Unsloth OK")
EOF
  local free_gb; free_gb="$(df -BG --output=avail "$WORK" | tail -1 | tr -dc 0-9)"
  (( free_gb >= 30 )) || echo "WARNING: only ${free_gb} GB free disk - the export needs ~30 GB."
  printf '\n\033[32mReady.\033[0m Next: bash %s train <data.jsonl>\n' "$0"
}

cmd_train() {
  local data="${1:-}"; shift || true
  [[ -f "$data" ]] || die "usage: $0 train <brt360_training_data.jsonl> [finetune.py options]"
  [[ -x "$VENV/bin/python" ]] || die "Run setup first: bash $0 setup"
  pgrep -f "finetune.py" >/dev/null && die "A training run is already going - see: bash $0 status"
  check_gpu
  data="$(readlink -f "$data")"
  mkdir -p "$OUT"; rm -f "$OUT"/DONE "$OUT"/FAILED
  [[ "$SCRIPT_DIR/finetune.py" -ef "$WORK/finetune.py" ]] || cp "$SCRIPT_DIR/finetune.py" "$WORK/finetune.py"
  step "Training started in the background - safe to close SSH. Log: $LOG"
  # shellcheck disable=SC2016
  nohup bash -c 'cd "$1" && shift && "$@" && touch DONE || touch FAILED' _ "$OUT" \
    "$VENV/bin/python" "$WORK/finetune.py" --data "$data" --out brt360-telecom "$@" >"$LOG" 2>&1 &
  echo "Expected: 15-30 min on an L4/A10/4090, ~45 min on a T4, then ~10 min export."
  echo "Watch: bash $0 status"
}

cmd_status() {
  if [[ -f "$OUT/DONE" ]]; then
    printf '\033[32mFINISHED\033[0m\n'; grep -E "Intent accuracy|Training done" "$LOG" || true
    ls -lh "$OUT"/brt360-telecom.gguf "$OUT"/brt360-telecom-holdout.jsonl
  elif [[ -f "$OUT/FAILED" ]]; then
    printf '\033[31mFAILED\033[0m - last lines of %s:\n' "$LOG"; tail -n 30 "$LOG"; exit 1
  elif pgrep -f "finetune.py" >/dev/null; then
    printf '\033[33mRUNNING\033[0m\n'
    tail -c 4000 "$LOG" | tr '\r' '\n' | grep -v '^\s*$' | tail -n 8
    nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader | sed 's/^/GPU use: /'
  else
    echo "No training run found. Start one: bash $0 train <data.jsonl>"
  fi
}

case "${1:-}" in
  setup)  cmd_setup ;;
  train)  shift; cmd_train "$@" ;;
  status) cmd_status ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
