# Runtime record

Development setup, September 12, 2026. No held-out run is frozen yet.

The model runs on the user's rented RTX A5000. Tasks execute in a separate local Docker VM because the Vast instance is itself an unprivileged container and does not support nested Docker. The serving endpoint binds to remote localhost; an SSH tunnel exposes it on local localhost only. No provider credential is sent to the GPU.

| Component | Verified configuration |
|---|---|
| GPU | NVIDIA RTX A5000, 24,564 MiB; compute capability 8.6 |
| Driver | 580.95.05 |
| Serving Python | 3.12.14, isolated virtual environment |
| vLLM | 0.10.2 |
| Torch | 2.8.0+cu128; CUDA tensor operation checked |
| Serving Transformers | 4.55.2 |
| Serving OpenAI client | 1.109.1 |
| Model | Qwen/Qwen3-4B-Instruct-2507 |
| Model/tokenizer revision | `cdbee75f17c01a7cc42f958dc650907174af0554` |
| Precision | bfloat16 |
| Context window | 16,384 tokens |
| Runtime options | Eager execution; GPU memory utilization 0.85; max sequences 8; Hermes tool-call parser |
| Local Python | 3.12.13; all package versions in `uv.lock` |
| Local Docker | Colima profile `context-fidelity`; 2 CPUs, 4 GiB RAM; no host mounts |
| Sandbox image | `python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2` |

The server is supervised as `context-fidelity-vllm`. Its logs and environment live under `/workspace/context-fidelity-serving` on the rented instance; that storage is not guaranteed to survive instance recycling. Persist experiment records locally.

The model's repository includes sampling defaults. Each experimental call explicitly supplies temperature, top-p, top-k, seed, and output budget; do not depend on server defaults. The server may return several tool calls despite `parallel_tool_calls=false`; the actor must execute them serially and enforce its actual call budget.

Local Docker commands must select the dedicated context or set `DOCKER_HOST`. CI uses its own normal Docker daemon. Neither the user's Docker default context nor their SSH configuration needs to change.

Development smoke: Inspect requested the single word `READY`; the model returned `READY` using 15 input and 2 output tokens. This verifies connectivity only, not task performance.
