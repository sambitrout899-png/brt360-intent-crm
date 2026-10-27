#!/usr/bin/env bash
# BRT 360 - the platform's own AI model, running on THIS server (no external API).
#
#   bash deploy/local_llm.sh install            # Ollama + base model + "brt360-telecom", private to this server
#   bash deploy/local_llm.sh status             # running? which models? memory
#   bash deploy/local_llm.sh bench              # how fast does it answer a real CRM request?
#   bash deploy/local_llm.sh model qwen2.5:7b-instruct   # rebuild brt360-telecom on a bigger/smaller base
#   bash deploy/local_llm.sh import /tmp/brt360-telecom.Q4_K_M.gguf   # use YOUR fine-tuned model (deploy/llm/README.md)
#   bash deploy/local_llm.sh adapter /tmp/brt360-telecom-lora.gguf [qwen2.5:7b-instruct]   # or just its LoRA adapter on the stock base
#   bash deploy/local_llm.sh uninstall
#
# Then in the app: Admin -> AI Settings -> "Our own model (this server)".
#
# The model server listens on 127.0.0.1:11434 only - not reachable from the internet;
# the BRT 360 API talks to it locally (LOCAL_LLM_BASE_URL, default http://127.0.0.1:11434/v1).
# Env options: BASE_MODEL (default: picked from RAM), MODEL_NAME [brt360-telecom], CONTEXT [8192]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_NAME="${MODEL_NAME:-brt360-telecom}"
CONTEXT="${CONTEXT:-8192}"
HOST="127.0.0.1:11434"
SUDO=""; [[ $EUID -ne 0 ]] && SUDO="sudo"

step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARNING: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

ram_mb()   { awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo; }
avail_mb() { awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo; }

# Small enough to answer in seconds on CPU, big enough to follow the CRM's JSON prompts.
pick_base() {
  local ram cores; ram="$(ram_mb)"; cores="$(nproc)"
  if   (( ram < 6000 ));                    then echo "qwen2.5:1.5b-instruct"
  elif (( ram < 16000 || cores < 8 ));      then echo "qwen2.5:3b-instruct"
  else                                           echo "qwen2.5:7b-instruct"
  fi
}

write_modelfile() {  # $1 = FROM (model tag or .gguf path), $2 = output file
  cat > "$2" <<EOF
FROM $1
PARAMETER temperature 0
PARAMETER top_p 0.9
PARAMETER num_ctx $CONTEXT
PARAMETER num_predict 1024
SYSTEM """You are BRT 360's telecom CRM assistant for Indian enterprise telecom operators.
You understand sales (leads, opportunities, quotes, orders), service assurance (tickets,
field work), billing (invoices, usage, balances), network (circuits, availability,
latency) and inventory (CPE, SIMs, assets). When asked for JSON you answer with one
valid JSON object only - no prose, no code fences."""
EOF
}

wait_up() {
  for _ in $(seq 1 30); do curl -fsS "http://$HOST/api/tags" >/dev/null 2>&1 && return 0; sleep 1; done
  die "Ollama did not start - check: journalctl -u ollama -n 50"
}

cmd_install() {
  local ram cores base
  ram="$(ram_mb)"; cores="$(nproc)"
  base="${BASE_MODEL:-$(pick_base)}"
  step "Server: ${cores} CPU cores, ${ram} MB RAM ($(avail_mb) MB free) -> base model: $base"
  (( ram < 3500 )) && die "At least 4 GB RAM is needed for a local model (this server has ${ram} MB)"
  command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | sed 's/^/GPU: /' \
    || echo "No GPU - the model runs on CPU (a few seconds per answer)."

  if ! command -v ollama >/dev/null; then
    step "Installing Ollama"
    curl -fsSL https://ollama.com/install.sh | sh
  else
    echo "Ollama already installed: $(ollama --version 2>/dev/null | head -1)"
  fi

  step "Configuring the model server (localhost only, kept in memory, one request at a time)"
  $SUDO mkdir -p /etc/systemd/system/ollama.service.d
  $SUDO tee /etc/systemd/system/ollama.service.d/brt360.conf >/dev/null <<EOF
[Service]
Environment="OLLAMA_HOST=$HOST"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_CONTEXT_LENGTH=$CONTEXT"
EOF
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable ollama >/dev/null 2>&1 || true
  $SUDO systemctl restart ollama
  wait_up

  step "Downloading $base (one time)"
  OLLAMA_HOST="$HOST" ollama pull "$base"
  build_model "$base"
  cmd_bench
  printf '\n\033[32mDone.\033[0m In BRT 360: Admin -> AI Settings -> "Our own model (this server)" -> Test -> Save.\n'
}

build_model() {  # $1 = base tag or gguf path
  step "Building '$MODEL_NAME' from $1"
  local mf; mf="$(mktemp)"
  write_modelfile "$1" "$mf"
  OLLAMA_HOST="$HOST" ollama create "$MODEL_NAME" -f "$mf"
  rm -f "$mf"
  OLLAMA_HOST="$HOST" ollama list
}

cmd_model() {
  local base="${1:-}"; [[ -n "$base" ]] || die "usage: $0 model <ollama tag, e.g. qwen2.5:7b-instruct or llama3.1:8b>"
  wait_up
  OLLAMA_HOST="$HOST" ollama pull "$base"
  build_model "$base"
  cmd_bench
}

cmd_import() {
  local gguf="${1:-}"
  [[ -n "$gguf" ]] || die "usage: $0 import /path/to/model.gguf (the file deploy/llm/finetune.py produces)"
  [[ -f "$gguf" ]] || die "No file at '$gguf'. Import is the step AFTER fine-tuning: train with deploy/llm/finetune.py on a GPU machine,
       copy the .gguf it writes to this server, then run import on that path. (See deploy/llm/README.md, section 2.)
       Your current model keeps working meanwhile."
  wait_up
  # keep the current model as a fallback tag
  if OLLAMA_HOST="$HOST" ollama show "$MODEL_NAME" >/dev/null 2>&1; then
    OLLAMA_HOST="$HOST" ollama cp "$MODEL_NAME" "$MODEL_NAME-previous" && echo "Previous model kept as $MODEL_NAME-previous"
  fi
  # A fine-tuned GGUF needs the base model's chat template (Qwen <|im_start|> format) to talk correctly.
  local base mf
  base="${BASE_MODEL:-$(OLLAMA_HOST="$HOST" ollama list | awk 'NR>1 && $1 ~ /^qwen2.5/ {print $1; exit}')}"
  [[ -n "$base" ]] || die "Base model not found - run: $0 install (or set BASE_MODEL=<the tag you fine-tuned from>)"
  step "Building '$MODEL_NAME' from $(basename "$gguf") with the chat template of $base"
  mf="$(mktemp)"
  write_modelfile "$(readlink -f "$gguf")" "$mf"
  {
    printf 'TEMPLATE """%s"""\n' "$(OLLAMA_HOST="$HOST" ollama show "$base" --template)"
    printf 'PARAMETER stop "<|im_start|>"\nPARAMETER stop "<|im_end|>"\n'
  } >> "$mf"
  OLLAMA_HOST="$HOST" ollama create "$MODEL_NAME" -f "$mf"
  rm -f "$mf"
  OLLAMA_HOST="$HOST" ollama list
  cmd_bench
}

cmd_bench() {
  step "Benchmark: one real CRM request through the OpenAI-compatible API"
  local body t0 t1 out
  body='{"model":"'"$MODEL_NAME"'","temperature":0,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":"Classify the telecom CRM request. Answer JSON {\"intent\": one of qualify_lead|log_complaint|create_quote, \"company\": string}."},{"role":"user","content":"Acme Telecom internet has been down since morning"}]}'
  for run in 1 2; do
    t0=$(date +%s%N)
    out="$(curl -fsS --max-time 300 "http://$HOST/v1/chat/completions" -H 'Content-Type: application/json' -d "$body")" || die "Model did not answer"
    t1=$(date +%s%N)
    printf 'run %s: %d ms  ->  %s\n' "$run" "$(( (t1 - t0) / 1000000 ))" "$(echo "$out" | python3 -c 'import sys,json; print(json.load(sys.stdin)["choices"][0]["message"]["content"][:160])')"
  done
  echo "(run 1 includes loading the model into memory; run 2 is the normal speed)"
}

cmd_adapter() {
  # $1 = LoRA adapter .gguf (convert_lora_to_gguf.py on the adapter finetune.py saves).
  # Newer Ollama refuses separate adapters, so the adapter is merged into the full-precision base here,
  # quantized, and imported like any fine-tuned model. Everything big is downloaded by this server.
  local lora="${1:-}"
  [[ -f "$lora" ]] || die "usage: $0 adapter /tmp/brt360-telecom-lora.gguf   (Qwen 2.5 7B adapters; BASE_REPO / BASE_PREFIX for others)"
  lora="$(readlink -f "$lora")"
  local merge=""
  for m in "$SCRIPT_DIR/llm/merge_lora.py" "$SCRIPT_DIR/merge_lora.py" "$(dirname "$lora")/merge_lora.py"; do [[ -f "$m" ]] && merge="$m" && break; done
  [[ -n "$merge" ]] || die "merge_lora.py not found next to this script or the adapter - copy deploy/llm/merge_lora.py there"
  local repo="${BASE_REPO:-Qwen/Qwen2.5-7B-Instruct-GGUF}" prefix="${BASE_PREFIX:-qwen2.5-7b-instruct-fp16}" parts="${BASE_PARTS:-4}"
  local rel="${LLAMA_CPP_RELEASE:-b11748}" work="${MERGE_DIR:-$HOME/brt360-merge}"
  mkdir -p "$work"; cd "$work"
  local free_gb; free_gb="$(df -BG --output=avail "$work" | tail -1 | tr -dc 0-9)"
  (( free_gb >= 34 )) || die "Only ${free_gb} GB free in $work - the merge needs ~34 GB for a while (MERGE_DIR=/bigger/disk to use another place)"
  command -v python3 >/dev/null || die "python3 is needed"
  wait_up

  step "1/6 llama.cpp tools ($rel, prebuilt - nothing is compiled)"
  if [[ ! -x "llama-$rel/llama-quantize" ]]; then
    curl -fL --retry 3 -o llama.tar.gz "https://github.com/ggml-org/llama.cpp/releases/download/$rel/llama-$rel-bin-ubuntu-x64.tar.gz"
    tar -xzf llama.tar.gz && rm -f llama.tar.gz
  fi
  local bin="$work/llama-$rel"

  step "2/6 Full-precision base from Hugging Face ($repo, ~14 GB - a few minutes on a server line)"
  if [[ ! -f base-f16.gguf ]]; then
    for i in $(seq 1 "$parts"); do
      local f; f="$(printf '%s-%05d-of-%05d.gguf' "$prefix" "$i" "$parts")"
      [[ -f "$f.done" ]] || { curl -fL --retry 5 -C - -o "$f" "https://huggingface.co/$repo/resolve/main/$f" && touch "$f.done"; }
    done
    step "3/6 Joining the $parts parts into one file"
    "$bin/llama-gguf-split" --merge "$(printf '%s-%05d-of-%05d.gguf' "$prefix" 1 "$parts")" base-f16.gguf
    rm -f "$prefix"-*-of-*.gguf "$prefix"-*.done
  fi

  step "4/6 Merging your adapter into the base"
  [[ -x venv/bin/python ]] || python3 -m venv venv || die "python3 -m venv failed - install it: apt-get install -y python3-venv"
  venv/bin/pip install -q numpy gguf
  venv/bin/python "$merge" --base base-f16.gguf --lora "$lora" --dry-run
  nice -n 19 venv/bin/python "$merge" --base base-f16.gguf --lora "$lora" --in-place

  step "5/6 Quantizing to Q4_K_M (CPU, low priority so the CRM stays responsive)"
  nice -n 19 "$bin/llama-quantize" base-f16.gguf "$MODEL_NAME-merged.Q4_K_M.gguf" Q4_K_M
  rm -f base-f16.gguf  # merged in place - not reusable for another adapter

  step "6/6 Importing into Ollama as '$MODEL_NAME' (the current one is kept as '$MODEL_NAME-previous')"
  OLLAMA_HOST="$HOST" ollama show qwen2.5:7b-instruct >/dev/null 2>&1 || OLLAMA_HOST="$HOST" ollama pull qwen2.5:7b-instruct  # its chat template
  BASE_MODEL="${BASE_MODEL:-qwen2.5:7b-instruct}" cmd_import "$work/$MODEL_NAME-merged.Q4_K_M.gguf"
  echo "Merged model file: $work/$MODEL_NAME-merged.Q4_K_M.gguf (delete it once you are happy - Ollama keeps its own copy)"
}

cmd_status() {
  step "Model server"
  systemctl --no-pager --lines=0 status ollama 2>/dev/null | head -3 || echo "ollama service not installed"
  curl -fsS "http://$HOST/api/tags" >/dev/null 2>&1 && echo "API: http://$HOST (OK)" || warn "not answering on $HOST"
  OLLAMA_HOST="$HOST" ollama list 2>/dev/null || true
  echo; echo "Loaded right now:"; OLLAMA_HOST="$HOST" ollama ps 2>/dev/null || true
  echo; echo "Memory: $(avail_mb) MB free of $(ram_mb) MB"
}

cmd_uninstall() {
  step "Removing the local model server"
  $SUDO systemctl disable --now ollama 2>/dev/null || true
  $SUDO rm -f /etc/systemd/system/ollama.service.d/brt360.conf /etc/systemd/system/ollama.service /usr/local/bin/ollama
  $SUDO systemctl daemon-reload
  echo "Model files are in /usr/share/ollama/.ollama (delete to free disk). Switch tenants back to External or Rules in Admin -> AI Settings."
}

case "${1:-}" in
  install)   cmd_install ;;
  status)    cmd_status ;;
  bench)     cmd_bench ;;
  model)     shift; cmd_model "${1:-}" ;;
  import)    shift; cmd_import "${1:-}" ;;
  adapter)   shift; cmd_adapter "${1:-}" "${2:-}" ;;
  uninstall) cmd_uninstall ;;
  *) sed -n '2,16p' "$0"; exit 1 ;;
esac
