"""Fine-tune BRT 360's own telecom CRM model (LoRA) and export it for the server.

Run on a machine with an NVIDIA GPU (a rented one is fine - an RTX 4090 / L4 /
A10 / A100 for 1-2 hours). Not on the CRM server: training needs a GPU,
answering does not.

    pip install unsloth                       # pulls torch, transformers, trl, datasets
    python finetune.py --data brt360_training_data.jsonl
    # -> brt360-telecom-gguf/*.Q4_K_M.gguf  -> copy to the server, then:
    #    bash deploy/local_llm.sh import /tmp/<file>.gguf

The data file comes from Admin -> AI Settings -> "Download training data"
(or: python -m scripts.export_training_data --out data.jsonl on the server).
Use the same --base family as the server runs (default Qwen 2.5 3B).
"""

from __future__ import annotations

import argparse
import inspect
import json
import random
import shutil
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", required=True, help="chat JSONL from the BRT 360 exporter")
    p.add_argument("--base", default="unsloth/Qwen2.5-3B-Instruct", help="e.g. unsloth/Qwen2.5-7B-Instruct, unsloth/Qwen2.5-1.5B-Instruct")
    p.add_argument("--epochs", type=float, default=2.0)
    p.add_argument("--max-seq", type=int, default=8192, help="must fit the CRM's long system prompt")
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--out", default="brt360-telecom")
    p.add_argument("--quant", default="q4_k_m", help="GGUF quantization for CPU serving (q4_k_m = good speed/quality)")
    p.add_argument("--holdout", type=float, default=0.05, help="share kept aside for evaluate.py")
    p.add_argument("--preset", choices=["default", "smart"], default="default",
                   help="smart = a bigger base (Qwen 2.5 7B), 3 epochs, LoRA rank 32: noticeably better reasoning, "
                        "needs ~24 GB VRAM to train and ~8 GB RAM on the CRM server, roughly 2x slower answers on CPU")
    args = p.parse_args()
    if args.preset == "smart":  # only what wasn't given on the command line (an explicit "--epochs 2" stays 2)
        import sys

        given = {a.split("=")[0] for a in sys.argv[1:] if a.startswith("--")}
        for key, value in (("base", "unsloth/Qwen2.5-7B-Instruct"), ("epochs", 3.0), ("rank", 32)):
            if f"--{key}" not in given:
                setattr(args, key, value)
    print(f"Base {args.base}, {args.epochs:g} epochs, LoRA rank {args.rank}")

    # Imported here so --help works without a GPU stack installed - unsloth first, so it can patch trl/transformers.
    from unsloth import FastLanguageModel, is_bfloat16_supported
    from unsloth.chat_templates import get_chat_template, train_on_responses_only
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    rows = [{"messages": json.loads(line)["messages"]} for line in Path(args.data).read_text(encoding="utf-8").splitlines() if line.strip()]
    random.Random(7).shuffle(rows)
    # held out from every kind of example, so the scorecard covers the hard ones too
    by_kind: dict[str, list] = {}
    for row in rows:
        by_kind.setdefault(kind_of(row), []).append(row)
    eval_rows, train_rows = [], []
    for group in by_kind.values():
        n = max(1, int(len(group) * args.holdout)) if len(group) > 3 else 0
        eval_rows += group[:n]
        train_rows += group[n:]
    random.Random(7).shuffle(train_rows)
    random.Random(7).shuffle(eval_rows)  # evaluate.py scores the first N - a mix of every kind
    Path(f"{args.out}-holdout.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in eval_rows), encoding="utf-8")
    print(f"{len(train_rows)} training / {len(eval_rows)} held-out examples (held-out saved for evaluate.py)")

    model, tokenizer = FastLanguageModel.from_pretrained(model_name=args.base, max_seq_length=args.max_seq, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(
        model, r=args.rank, lora_alpha=args.rank, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=7,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="qwen-2.5" if "qwen" in args.base.lower() else "llama-3.1")

    def to_text(batch):
        return {"text": [tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False) for m in batch["messages"]]}

    train = Dataset.from_list(train_rows).map(to_text, batched=True, remove_columns=["messages"])
    evals = Dataset.from_list(eval_rows).map(to_text, batched=True, remove_columns=["messages"])

    # Keyword names moved between TRL / transformers versions - pass whichever this install accepts.
    cfg_params = inspect.signature(SFTConfig.__init__).parameters
    seq_key = "max_length" if "max_length" in cfg_params else "max_seq_length"
    eval_key = "eval_strategy" if "eval_strategy" in cfg_params else "evaluation_strategy"
    config = {
        "dataset_text_field": "text", seq_key: args.max_seq, "packing": False,
        "per_device_train_batch_size": 1, "gradient_accumulation_steps": 8, "num_train_epochs": args.epochs,
        "learning_rate": args.lr, "lr_scheduler_type": "cosine", "warmup_ratio": 0.03, "weight_decay": 0.01,
        "optim": "adamw_8bit", "bf16": is_bfloat16_supported(), "fp16": not is_bfloat16_supported(),
        "logging_steps": 10, eval_key: "epoch", "save_strategy": "no", "output_dir": f"{args.out}-checkpoints",
        "seed": 7, "report_to": "none",
    }
    tok_key = "processing_class" if "processing_class" in inspect.signature(SFTTrainer.__init__).parameters else "tokenizer"
    trainer = SFTTrainer(
        model=model, train_dataset=train, eval_dataset=evals,
        args=SFTConfig(**{k: v for k, v in config.items() if k in cfg_params}), **{tok_key: tokenizer},
    )
    # Learn only the answers - the long system prompt is context, not something to imitate.
    if "qwen" in args.base.lower():
        trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n")
    else:
        trainer = train_on_responses_only(trainer, instruction_part="<|start_header_id|>user<|end_header_id|>\n\n",
                                          response_part="<|start_header_id|>assistant<|end_header_id|>\n\n")
    stats = trainer.train()
    print(f"Training done in {stats.metrics.get('train_runtime', 0) / 60:.0f} min, final loss {stats.metrics.get('train_loss')}")

    model.save_pretrained(f"{args.out}-lora")
    tokenizer.save_pretrained(f"{args.out}-lora")
    print(f"Intent accuracy on held-out requests (by kind): {intent_accuracy(model, tokenizer, eval_rows)}")

    model.save_pretrained_gguf(f"{args.out}-gguf", tokenizer, quantization_method=args.quant)
    ggufs = sorted(Path(".").glob(f"{args.out}-gguf*/*.gguf")) + sorted(Path(".").glob("*.gguf"))
    biggest = max((g for g in ggufs if g.name != f"{args.out}.gguf"), key=lambda g: g.stat().st_size)
    shutil.copy(biggest, f"{args.out}.gguf")
    print(f"GGUF written to {args.out}.gguf - copy it to the server and run:")
    print(f"  bash /opt/brt360/deploy/local_llm.sh import /tmp/{args.out}.gguf")


def kind_of(row) -> str:
    """normal / hard (look-alike or vague) / unknown (out of scope) / plan / c360 - from the expected answer."""
    try:
        answer = json.loads(row["messages"][-1]["content"])
    except ValueError:
        return "other"
    if "steps" in answer:
        return "plan"
    if "ops" in answer:
        return "c360"
    if answer.get("intent_type") == "UNKNOWN":
        return "unknown"
    if answer.get("alternatives") or "Not " in str(answer.get("rationale", "")):
        return "hard"
    return "normal"


def answer_key(answer: dict) -> object:
    """What must match: the intent - or, for a plan, the codes of its steps in order."""
    if "steps" in answer:
        return [s.get("intent_type") for s in answer.get("steps") or [] if isinstance(s, dict)]
    return answer.get("intent_type")


def intent_accuracy(model, tokenizer, rows) -> str:
    """Quick check on the held-out rows, by kind; evaluate.py on the server gives the full scorecard."""
    from unsloth import FastLanguageModel

    FastLanguageModel.for_inference(model)
    score: dict[str, list[int]] = {}
    for row in rows:
        kind = kind_of(row)
        if kind == "c360":
            continue
        msgs = row["messages"]
        want = answer_key(json.loads(msgs[-1]["content"]))
        ids = tokenizer.apply_chat_template(msgs[:-1], add_generation_prompt=True, return_tensors="pt").to(model.device)
        out = model.generate(input_ids=ids, max_new_tokens=700 if kind == "plan" else 300, do_sample=False)
        text = tokenizer.decode(out[0][ids.shape[1]:], skip_special_tokens=True)
        try:
            got = answer_key(json.loads(text[text.find("{"): text.rfind("}") + 1]))
        except ValueError:
            got = None
        tally = score.setdefault(kind, [0, 0])
        tally[0] += got == want
        tally[1] += 1
        print(("OK  " if got == want else "MISS"), f"[{kind}]", msgs[1]["content"][-70:].replace("\n", " "), "->", got)
    return ", ".join(f"{kind} {ok}/{total}" for kind, (ok, total) in sorted(score.items()))


if __name__ == "__main__":
    main()
