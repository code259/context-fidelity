# Runtime record

Verified September 12, 2026, in America/Los_Angeles. Artifact timestamps use UTC. Held-out run `heldout-002` was frozen at `2026-09-13T04:08:37.688635+00:00` against generation commit `8f8fc08`.

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

The first development attempt, `dev-pilot-001`, preserved eight failed actor attempts and produced no histories or reports. The SSH client timed out while the supervised model server stayed healthy. A new localhost-only tunnel uses 20-second server keep-alives, three missed replies as its failure limit, and an explicit control socket. A subsequent health check and two-token `READY` response succeeded. The failed run remains intact.

The replacement, `dev-pilot-002`, completed eight histories and summaries in 170.6 seconds, then 32 reports in 79.6 seconds. It had no technical failures. These timings include orchestration and sandbox work; they are not a model throughput benchmark. No paid OpenAI calls were used.

`heldout-001` was prepared but never collected. A CLI fix deferred Matplotlib initialization until offline analysis, avoiding unnecessary font-cache work during preparation. That changed the frozen source, so the active run uses a new ID, `heldout-002`. The amendment records that no held-out generation preceded this fix.

`heldout-002` completed 24 histories and summaries in 486.4 seconds, followed by 192 reports in 475.4 seconds. These durations run from each phase's start record to its final freeze and exclude the intervening audit. Every planned generation completed without a technical failure. No paid OpenAI calls were used. See the [results record](experiments/results.md) for analysis and human-review status.

All experiment calls use Inspect's OpenAI-compatible chat adapter with `max_retries=0` and a 90-second client timeout. The client sends a nonsecret placeholder to the private endpoint. A cancellation sidecar is authoritative even when the upstream Inspect log reports overall success. Inspect logs, serialized requests/responses, and run manifests remain local.
