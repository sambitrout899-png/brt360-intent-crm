# BRT 360's own telecom CRM model

BRT 360 can run its AI on a model hosted **on your own server**: no external API,
no per-call fees, and no customer data leaves the machine. Each tenant admin
chooses what powers their AI in **Admin → AI Settings**:

| Option | What answers | When to use |
|---|---|---|
| External API | OpenAI (tenant key or platform key) | best raw quality, data goes to the provider |
| **Our own model** | `brt360-telecom` on this server | private, free per call, gets better with training |
| Built-in rules | no model | fastest, no AI, still handles the standard phrasings |

Any model failure (down, slow, bad answer) automatically falls back to the
built-in rules, so the CRM never stops working because of the model.

## 1. Run the model on the CRM server (10 minutes)

```bash
bash /opt/brt360/deploy/local_llm.sh install
```

* Installs Ollama, listening on `127.0.0.1:11434` only (not reachable from the internet).
* Picks a base model that fits the server: Qwen 2.5 **1.5B** (< 6 GB RAM), **3B** (< 16 GB or < 8 cores), **7B** (bigger).
  Override: `BASE_MODEL=qwen2.5:7b-instruct bash deploy/local_llm.sh install`.
* Builds **`brt360-telecom`** on top of it (telecom CRM instructions, deterministic answers, 8k context)
  and runs a benchmark.

Then: **Admin → AI Settings → Our own model → Test → Save**.

On a CPU-only server expect roughly 3–15 s per answer (first call after a restart is slower -
the model loads into memory). That is fine when running an intent; for **as-you-type
suggestions** the app uses the fast rules unless you tick "use it for live suggestions too".
A GPU, or a smaller base model, makes it faster.

Other commands: `status`, `bench`, `model <tag>` (switch base), `import <file.gguf>`, `uninstall`.

## 2. Make it *your* model: fine-tune on telecom CRM data

Day one, `brt360-telecom` is a general open model with telecom instructions. Fine-tuning
teaches it exactly the CRM's jobs - your intents, your products, how your people phrase things.

1. **Get the data** - Admin → AI Settings → **Download training data**
   (or on the server: `cd /opt/brt360/backend && .venv/bin/python -m scripts.export_training_data --out /tmp/train.jsonl`).
   It contains every request your users ran successfully, plus ~1,000 generated telecom
   examples covering all 33 intents and Configure 360. The real part grows with usage -
   re-train every month or two. *It contains customer wording: handle it like customer data.*

2. **Train** on any machine with an NVIDIA GPU (rent one for ~1–2 h: Hostinger GPU, RunPod, E2E, Lambda, AWS g5).
   Easiest - from your Windows PC, against a rented Ubuntu GPU server (16 GB+ VRAM, 40 GB disk):
   ```powershell
   cd deploy\llm
   .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action setup     # driver check, Python, Unsloth (~10 min, once)
   .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action train     # uploads Downloads\brt360_training_data.jsonl
   .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action status    # repeat until FINISHED
   .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action deliver   # model -> CRM server /tmp, then step 3 + 4
   ```
   Or by hand on the GPU machine:
   ```bash
   pip install unsloth
   python finetune.py --data train.jsonl                       # Qwen 2.5 3B, matches the default server model
   python finetune.py --data train.jsonl --base unsloth/Qwen2.5-7B-Instruct   # if the server runs 7B
   ```
   Output: `brt360-telecom.gguf` (~2 GB for 3B) and `brt360-telecom-holdout.jsonl`.

3. **Install it** on the CRM server (`deliver` above already did the copy):
   ```bash
   scp brt360-telecom.gguf root@<server>:/tmp/brt360-telecom.gguf
   scp brt360-telecom-holdout.jsonl root@<server>:/tmp/
   bash /opt/brt360/deploy/local_llm.sh import /tmp/brt360-telecom.gguf
   ```
   The previous model is kept as `brt360-telecom-previous` - nothing to change in the app.

4. **Check it's better** before relying on it:
   ```bash
   python3 /opt/brt360/deploy/llm/evaluate.py --data /tmp/brt360-telecom-holdout.jsonl
   python3 /opt/brt360/deploy/llm/evaluate.py --data /tmp/brt360-telecom-holdout.jsonl --model brt360-telecom-previous
   ```
   If the new one scores worse, roll back: `ollama cp brt360-telecom-previous brt360-telecom`.

## 5. Make it think better (reasoned training data, Oct 2026)

The training file now teaches more than "which action": every answer starts with a
specific **rationale** (what is asked, what is given, which look-alike it is *not*
and why, what is still missing), so the model reasons before it commits. It also
holds **vague requests** (best guess + lower confidence + alternatives),
**out-of-scope requests** (`UNKNOWN` instead of a forced action), **multi-step
plans** in the planning agent's own prompt, and examples for **every** action
(INT-01…INT-109). `--style plain` exports the original examples.

```powershell
# 1. new data: Admin -> AI Settings -> Download training data  (now "reasoned" by default)
# 2. train - the bigger base reasons noticeably better (needs ~24 GB VRAM; the B200 is plenty)
.\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action train -Extra "--preset smart"
```
or by hand: `python finetune.py --data train.jsonl --preset smart` (Qwen 2.5 7B, 3 epochs, LoRA rank 32).
Without `--preset smart` it stays on 3B - still better than before, just less so.

The CRM server then needs **~8 GB free RAM** for the 7B model, and answers take
roughly **twice as long on CPU** (the rationale adds a few seconds too). Check first:
`free -g` on the server. The training log and `evaluate.py` now score by kind -
plain requests, look-alikes/vague, out of scope, multi-step plans - so you can see
exactly where the new model is better before switching (and roll back as in step 4).

## Platform settings (backend/.env, optional)

```
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1   # any OpenAI-compatible server (Ollama, vLLM, llama.cpp)
LOCAL_LLM_MODEL=brt360-telecom
LOCAL_LLM_TIMEOUT_SECONDS=90
DEFAULT_LLM_PROVIDER=external                  # for tenants that never chose: external | local | rules
```

Where the model runs is a platform decision (this file), not a tenant one - tenants only
choose *whether* to use it.
