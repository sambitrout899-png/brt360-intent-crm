"""Merge a LoRA adapter (.gguf) into a full-precision base model (.gguf), in place.

Newer Ollama versions refuse separate adapters ("LoRA adapters are no longer
supported"), and the llama.cpp release builds don't ship llama-export-lora - so
this does the merge itself: W += (alpha / r) * B @ A for every adapted weight,
one tensor at a time (memory-mapped; a few hundred MB of RAM, no GPU).

    pip install numpy gguf
    python3 merge_lora.py --base qwen2.5-7b-instruct-f16.gguf --lora brt360-telecom-lora.gguf --in-place

The base must be one file in F16/BF16/F32 (join split downloads first with
llama-gguf-split --merge). It is CHANGED in place - keep the download if you want
the original. Then quantize (llama-quantize ... Q4_K_M) and import into Ollama
(deploy/local_llm.sh import).
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", required=True, help="full-precision base .gguf (single file) - modified in place")
    p.add_argument("--lora", required=True, help="LoRA adapter .gguf (convert_lora_to_gguf.py)")
    p.add_argument("--in-place", action="store_true", help="confirm: the base file is overwritten with the merged weights")
    p.add_argument("--dry-run", action="store_true", help="only check names, shapes and types")
    args = p.parse_args()
    if not (args.in_place or args.dry_run):
        p.error("pass --in-place to confirm the base file is modified (or --dry-run to only check)")

    from gguf import GGUFReader, GGMLQuantizationType as Q

    lora = GGUFReader(args.lora)
    alpha_field = lora.fields.get("adapter.lora.alpha")
    alpha = float(alpha_field.parts[alpha_field.data[0]][0]) if alpha_field else None
    pairs: dict[str, dict[str, np.ndarray]] = {}
    for t in lora.tensors:
        for suffix in (".lora_a", ".lora_b"):
            if t.name.endswith(suffix):
                pairs.setdefault(t.name[: -len(suffix)], {})[suffix[1:]] = t.data
    broken = [n for n, ab in pairs.items() if set(ab) != {"lora_a", "lora_b"}]
    if broken:
        print(f"Adapter is incomplete - no A/B pair for: {broken[:5]}")
        return 2

    base = GGUFReader(args.base, "r" if args.dry_run else "r+")
    by_name = {t.name: t for t in base.tensors}
    missing = [n for n in pairs if n not in by_name]
    if missing:
        print(f"The base has no tensor for {len(missing)} adapter weights, e.g. {missing[:3]} - is it the model the adapter was trained on?")
        return 2
    full = (Q.F16, Q.BF16, Q.F32)
    quantized = sorted({by_name[n].tensor_type.name for n in pairs if by_name[n].tensor_type not in full})
    if quantized:
        print(f"The base is quantized ({', '.join(quantized)}) - use the full-precision (f16) download")
        return 2

    started = time.time()
    for i, (name, ab) in enumerate(sorted(pairs.items()), 1):
        a, b = ab["lora_a"].astype(np.float32), ab["lora_b"].astype(np.float32)  # A: (r, in), B: (out, r)
        target = by_name[name]
        w = target.data  # (out, in), memory-mapped into the base file
        if b.shape[0] != w.shape[0] or a.shape[1] != w.shape[1] or a.shape[0] != b.shape[1]:
            print(f"Shape mismatch on {name}: base {w.shape}, A {a.shape}, B {b.shape}")
            return 2
        if args.dry_run:
            continue
        scale = (alpha / a.shape[0]) if alpha else 1.0
        if target.tensor_type == Q.BF16:  # bf16 is stored as uint16 - widen to f32, add, round back
            wide = (w.astype(np.uint32) << 16).view(np.float32) + scale * (b @ a)
            w[...] = (wide.view(np.uint32) + 0x7FFF + ((wide.view(np.uint32) >> 16) & 1) >> 16).astype(np.uint16)
        else:
            w[...] = (w.astype(np.float32) + scale * (b @ a)).astype(w.dtype)
        if i % 28 == 0 or i == len(pairs):
            print(f"  merged {i}/{len(pairs)} weights ({time.time() - started:.0f}s)", flush=True)
    if not args.dry_run:
        base.data.flush()
    print(f"{'Checked' if args.dry_run else 'Merged'} {len(pairs)} adapted weights (alpha {alpha}, rank {next(iter(pairs.values()))['lora_a'].shape[0]})"
          f"{'' if args.dry_run else ' into ' + args.base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
