# GPU training on this machine (Windows + AMD Radeon AI PRO R9700)

Measured 2026-11-01. Machine: Windows 11 Pro 26300, 12-core CPU, AMD Radeon AI PRO R9700
(RDNA4, `gfx1201`, 32 GB), AMD PRO driver 26.10.36 (WDDM 32.0.31036.15, Aug 2026),
Microsoft Store Python 3.13.14 (the only interpreter installed; no `py` launcher, no 3.11/3.12).

## What works

**Native ROCm PyTorch on Windows works.** AMD ships Windows `win_amd64` wheels of PyTorch
built on ROCm 7.13 (TheRock) for RDNA4, with cp311-cp313 tags, so no second Python and no
WSL/driver change was needed. `torch.cuda.is_available()` is `True`, the device is reported
as `AMD Radeon AI PRO R9700` (31.9 GB, 32 CUs), MIOpen is available (`cudnn.version()` =
3005001), and bf16 is reported as supported.

Options considered:

| Option | Status |
|---|---|
| ROCm 7.13 preview wheels, `repo.amd.com/rocm/whl/gfx120X-all/` (torch 2.11.0+rocm7.13.0) | **Installed, working, Python 3.11-3.13 (docs say 3.14 too)** |
| ROCm 7.2.1 "stable" wheels, `repo.radeon.com/rocm/windows/rocm-rel-7.2.1/` (torch 2.9.1) | Viable but **cp312-only** and needs the ROCm SDK wheels installed separately; not usable with the installed 3.13 |
| `torch-directml` 0.2.5 (Sept 2024, pins torch 2.4.1, cp38-cp312) | Fallback only: old torch, no cp313 wheel, no RNN/attention kernels, much slower. Not installed. |

## Install (exact commands)

```powershell
cd C:\Users\josh1\Documents\Code\RocketLeague-Analytics
python -m venv .venv-torch            # Python 3.13 system interpreter
.venv-torch\Scripts\python -m pip install --upgrade pip
.venv-torch\Scripts\python -m pip install --index-url https://repo.amd.com/rocm/whl/gfx120X-all/ torch torchvision torchaudio
# pin instead if you want reproducibility:
#   "torch==2.11.0+rocm7.13.0" "torchvision==0.26.0+rocm7.13.0" "torchaudio==2.11.0+rocm7.13.0"
.venv-torch\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Installs `torch 2.11.0+rocm7.13.0`, `rocm-sdk-core 7.13.0`, `rocm-sdk-libraries-gfx120X-all 7.13.0`
(~2-3 GB download, ~10 min). The ROCm runtime ships inside the venv; nothing system-wide,
no admin. The venv lives at `.venv-torch` (gitignored). Note: the first `import torch` after
install takes ~30 s (one-off kernel cache warm-up); afterwards it is a few seconds.

Docs: https://rocm.docs.amd.com/en/7.13.0-preview/frameworks/pytorch/install.html
(Windows 11 is listed, Radeon AI PRO R9700 / gfx1201 is in the supported list; the matrix names
Adrenalin 26.5.1 as the reference Windows driver, the installed PRO 26.10.36 is newer and works).

## Measured numbers

4096x4096 matmul (10-iter mean, synchronised):

| | time | throughput |
|---|---|---|
| GPU fp32 | 14.8 ms | 9.3 TFLOPS |
| GPU fp16 | 4.4 ms | 31.3 TFLOPS |
| GPU bf16 | 5.1 ms | 27.0 TFLOPS |
| CPU fp32 (same wheel, 12 threads) | 14 062 ms | 0.01 TFLOPS |

GPU vs CPU is ~950x on the matmul, but the CPU side is misleading: the ROCm wheel's CPU
BLAS is clearly unoptimised (a 12-core CPU with MKL/OpenBLAS would do this in ~100-300 ms).
Do not use this venv for CPU-only work; the project's normal venv is faster for that.

Training-step timings (GPU vs CPU in this wheel) are in the table below.

| Training step (fwd+bwd+Adam, fp32 unless noted) | GPU | CPU (this wheel) | speedup |
|---|---|---|---|
| MLP 512-1024-1024-1, batch 4096 | 4.9 ms | 6 042 ms | ~1200x |
| 2-layer GRU, H=128, batch 8 x **9 000 frames** x 64 feats | 7 345 ms | ~1 590 000 ms (extrapolated from T=1000) | ~215x |
| 4-layer Transformer encoder, d=128, batch 8 x 1 024 frames | 34.2 ms | ~110 700 ms (extrapolated from batch 2) | ~3200x |
| same Transformer, bf16 autocast | 22.9 ms | | |

Peak GPU memory for all of the above: 1.1 GB. The CPU column is the ROCm wheel's CPU path
and is pessimistic by roughly 50-100x versus a normal CPU PyTorch build; the GPU column is real.

The notable number is the GRU: 7.3 s per step over full 9k-frame sequences on the GPU, because
an RNN is a 9 000-step serial dependency and the MIOpen RNN kernel on Windows is not fast.
The Transformer over 1k-frame windows does a comparable amount of work in 34 ms.
GRU variants (see below) were timed to decide how to use RNNs on this card.

| 2-layer GRU H=128, F=64, GPU | per step |
|---|---|
| batch 8, T=9000, train, MIOpen (default) | 6 979 ms |
| batch 8, T=9000, train, native kernels (`cudnn.enabled=False`) | 20 283 ms (3x worse: keep MIOpen on) |
| batch 8, T=9000, forward only | 3 757 ms |
| **batch 64**, T=9000, train | **7 266 ms** (8x the throughput of batch 8, same wall time) |
| batch 8, T=1000, train | 787 ms (linear in T: ~0.4 ms per frame per layer-pass) |
| batch 8, T=9000, train, bf16 autocast | 4 052 ms (1.7x faster) |

Peak memory for the batch-64 run: 8 GB. RNN cost on this stack is per-timestep launch latency,
not FLOPs: wall time barely moves from batch 8 to 64, so **train GRUs with the largest batch that
fits (64-128 replays), in bf16, and downsample frames** (e.g. 30 Hz -> 10 Hz cuts a step from
~7 s to ~2.3 s). At batch 64 that is roughly 1 000 replays/epoch in ~2 min at 30 Hz.

## Limitations

- **Preview software.** ROCm 7.13 on Windows is a "technology preview"; the wheel index is
  updated frequently and unpinned installs can change under you. Pin versions for experiments.
- **Slow CPU path in this wheel** (see above). Keep CPU preprocessing in the regular venv or
  do it in NumPy; only put the model on `cuda`.
- **Windows ROCm has no Triton**, so `torch.compile` (Inductor) is not available on the GPU
  (see op-check results below); run eager mode. Flash-attention via Triton is likewise absent;
  `scaled_dot_product_attention` falls back to the math/MIOpen kernels, which work but are not
  flash-speed.
- `torch.cuda` is the HIP backend: device string is `"cuda"`, `get_device_capability()`
  returns `(12, 0)`; anything checking for NVIDIA-specific features (NCCL, cuDNN version
  ranges, `nvidia-smi`) will not apply.
- Mixed precision: fp16 and bf16 matmul both work at ~3x fp32 throughput; use
  `torch.autocast("cuda", dtype=torch.bfloat16)` (no GradScaler needed) as the default.
- Memory: 31.9 GB visible to PyTorch. No unified memory tricks; standard CUDA caching allocator semantics.
- Full-sequence Transformer attention over 9k frames is O(T^2): 9000^2 x heads x fp32 is ~1.3 GB
  per layer per sample just for attention scores, so chunk/window the sequence rather than attending over it whole.

## Recommendation for replay sequence models (GRU / Transformer over ~9k frames)

1. Use `.venv-torch` and `device="cuda"`; keep feature extraction / parquet loading in the main venv or NumPy.
2. Default to `bf16` autocast, eager mode, `batch_first=True`, `nn.GRU`/`nn.LSTM` (MIOpen RNN kernels work).
3. For GRUs, the step cost is set by sequence length, not batch: use batch 64-128 full replays
   (`pack_padded_sequence` for variable length; ~8 GB at batch 64), bf16 autocast, keep MIOpen
   enabled, and downsample frames where the task allows. Expect ~7 s/step at 9k frames, ~2.3 s at 3k.
4. Prefer a Transformer (or temporal conv) over 512-2048-frame windows when per-frame targets allow it:
   34 ms/step fp32 (23 ms bf16) for batch 8 x 1024 frames, versus seconds for a full-length GRU step.
   Avoid full 9k x 9k attention.
5. Don't rely on `torch.compile`; if a kernel is unsupported you get a clear HIP error rather than silent CPU fallback.
6. Pin the three wheel versions in any requirements file used for GPU experiments.

## Op support checks (torch 2.11.0+rocm7.13.0, Windows)

| Check | Result |
|---|---|
| `scaled_dot_product_attention` fp32 / fp16 / causal | ok |
| `nn.LSTM`, `nn.GRU`, `pack_padded_sequence` | ok (MIOpen RNN) |
| `nn.Conv1d` | ok |
| cumsum / sort / topk | ok |
| `torch.amp.GradScaler("cuda")` | ok |
| bf16 / fp16 autocast through LayerNorm + attention (Transformer step) | ok |
| `torch.compile` | **fails**: `TritonMissing` (no Triton on Windows ROCm); use eager |

Harmless warnings printed on first use: `xnack 'Off' was requested for a processor that does not
support it!` and `triton not found; flop counting will not work for triton kernels`.

Scripts used: `bench_gpu.py` / `gru_variants.py` (session scratchpad, not in repo); the numbers
above are single-run means on an otherwise idle machine.
