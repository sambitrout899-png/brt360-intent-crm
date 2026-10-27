<#
BRT 360 - fine-tune the telecom CRM model on a rented GPU server, driven from this PC.

  .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action setup     # once: driver check, Python, Unsloth
  (non-standard SSH port, e.g. Hostinger GPU: add -Port 30130 -GpuHost ubuntu@<ip>)
  .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action train     # uploads the data, starts training (background)
  .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action train -Extra "--preset smart"   # bigger 7B base: reasons better
  .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action status    # progress; re-run until FINISHED
  .\train_on_gpu.ps1 -GpuHost root@<gpu-ip> -Action deliver   # copies the model GPU -> CRM server /tmp (via this PC)

The model never needs to come to this PC's disk; nothing on the CRM server changes until you run
`local_llm.sh import` there yourself.
#>
param(
    [Parameter(Mandatory = $true)][string]$GpuHost,
    [ValidateSet("setup", "train", "status", "deliver")][string]$Action = "status",
    [string]$Data = "$env:USERPROFILE\Downloads\brt360_training_data.jsonl",
    [string]$CrmHost = "root@187.127.132.149",
    [string]$Base = "unsloth/Qwen2.5-3B-Instruct",
    [int]$Port = 22,
    # more finetune.py options, e.g. -Extra "--preset smart" (the 7B base, 3 epochs - better reasoning)
    [string]$Extra = ""
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$remote = "brt360-train"   # relative to the GPU server's home directory

function Invoke-Gpu([string]$cmd, [switch]$Tty) {
    # -t lets sudo ask for the password on servers where the login user is not root
    if ($Tty) { ssh -t -p $Port -o ServerAliveInterval=30 $GpuHost $cmd }
    else { ssh -p $Port -o ServerAliveInterval=30 $GpuHost $cmd }
    if ($LASTEXITCODE -ne 0) { throw "Command failed on $GpuHost (exit $LASTEXITCODE): $cmd" }
}

switch ($Action) {
    "setup" {
        Invoke-Gpu "mkdir -p ~/$remote"
        scp -P $Port "$here\gpu_train.sh" "$here\finetune.py" "${GpuHost}:~/$remote/"
        Invoke-Gpu "sed -i 's/\r$//' ~/$remote/gpu_train.sh && bash ~/$remote/gpu_train.sh setup" -Tty
    }
    "train" {
        if (-not (Test-Path $Data)) { throw "Training data not found: $Data (Admin -> AI Settings -> Download training data)" }
        scp -P $Port "$here\gpu_train.sh" "$here\finetune.py" $Data "${GpuHost}:~/$remote/"
        $name = Split-Path $Data -Leaf
        # --base only when chosen here, so finetune.py's own default (or --preset smart) applies otherwise
        $baseArg = if ($PSBoundParameters.ContainsKey("Base")) { "--base $Base" } else { "" }
        Invoke-Gpu "sed -i 's/\r$//' ~/$remote/gpu_train.sh && bash ~/$remote/gpu_train.sh train ~/$remote/$name $baseArg $Extra"
    }
    "status" {
        Invoke-Gpu "bash ~/$remote/gpu_train.sh status"
    }
    "deliver" {
        Invoke-Gpu "test -f ~/$remote/out/DONE || (echo 'Training not finished yet' >&2; exit 1)"
        Write-Host "Copying the model (~2 GB) $GpuHost -> ${CrmHost}:/tmp/ ..." -ForegroundColor Cyan
        foreach ($f in "brt360-telecom.gguf", "brt360-telecom-holdout.jsonl") {
            # -3: route through this PC, so the two servers need no SSH keys for each other.
            # In scp:// URLs the path after "host:port/" is relative to the login's home directory.
            scp -3 "scp://${GpuHost}:$Port/$remote/out/$f" "${CrmHost}:/tmp/$f"
            if ($LASTEXITCODE -ne 0) { throw "Copy of $f failed" }
        }
        Write-Host @"

Delivered to ${CrmHost}:/tmp/. Now on the CRM server:
  bash /opt/brt360/deploy/local_llm.sh import /tmp/brt360-telecom.gguf
  python3 /opt/brt360/deploy/llm/evaluate.py --data /tmp/brt360-telecom-holdout.jsonl
  python3 /opt/brt360/deploy/llm/evaluate.py --data /tmp/brt360-telecom-holdout.jsonl --model brt360-telecom-previous
Worse than before? Roll back: ollama cp brt360-telecom-previous brt360-telecom

Then stop or cancel the GPU server so it stops billing.
"@ -ForegroundColor Green
    }
}
