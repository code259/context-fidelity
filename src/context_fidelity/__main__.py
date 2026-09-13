"""Command-line access to preparation, Docker validation, and model collection."""

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from context_fidelity.adapters.model import OfficialTokenizer, local_model
from context_fidelity.adapters.sandbox import DockerSandbox, SandboxError
from context_fidelity.pipeline import (
    PipelineServices,
    collect_study,
    load_config,
    load_study,
    prepare_study,
    report_study,
    validate_tasks,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reproducible context-fidelity experiment")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--runtime-dir", type=Path, default=Path("../.local-runtime"))
    parser.add_argument("--endpoint", default="http://127.0.0.1:18081/v1")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser(
        "prepare", help="freeze tasks, settings, source, and planned cells"
    )
    prepare.add_argument("--run-dir", type=Path, required=True)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--split", choices=("dev", "heldout"), required=True)
    prepare.add_argument("--tasks", type=Path)
    prepare.add_argument("--validation", type=Path)
    prepare.add_argument("--seed", type=int)
    prepare.add_argument("--repetitions", type=int, choices=(1, 2))
    for command in ("collect", "report"):
        phase = commands.add_parser(command, help=f"run the {command} phase exactly once")
        phase.add_argument("--run-dir", type=Path, required=True)
        if command == "report":
            phase.add_argument("--summary-audit", type=Path)
    validate = commands.add_parser("validate-tasks", help="validate fixtures using real Docker")
    validate.add_argument("--tasks", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)
    return parser


def _path(root: Path, value: Path) -> Path:
    return (root / value).resolve()


def _runtime(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_DATA_HOME"] = str(directory / "data")
    os.environ["XDG_CACHE_HOME"] = str(directory / "cache")
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["INSPECT_DISPLAY"] = "none"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.repo_root.resolve()
    runtime = _path(root, args.runtime_dir)
    try:
        _runtime(runtime)
        if args.command == "prepare":
            plan = prepare_study(
                root,
                config_path=_path(root, args.config),
                task_dir=_path(root, args.tasks or Path("tasks") / args.split),
                run_dir=_path(root, args.run_dir),
                run_id=args.run_id,
                split=args.split,
                created_at=datetime.now(UTC).isoformat(),
                seed=args.seed,
                repetitions=args.repetitions,
                validation_path=_path(root, args.validation) if args.validation else None,
            )
            print(
                f"Prepared {plan.run_id}: {len(plan.histories)} histories, "
                f"{len(plan.reports)} reports"
            )
            return 0
        if args.command == "validate-tasks":
            config = load_config(_path(root, args.config))
            validation = validate_tasks(
                _path(root, args.tasks),
                _path(root, args.output),
                lambda task, environment: DockerSandbox(
                    task,
                    environment,
                    image=config.sandbox_image,
                    test_timeout=config.test_timeout_seconds,
                ),
            )
            invalid = [record.task_id for record in validation.records if not record.valid]
            print(
                f"Validated {len(validation.records)} tasks; {len(invalid)} invalid; {args.output}"
            )
            for record in validation.records:
                if not record.valid:
                    print(
                        f"{record.task_id}: {record.error or 'fixture assertions failed'}",
                        file=sys.stderr,
                    )
            return int(bool(invalid))
        run_dir = _path(root, args.run_dir)
        plan = load_study(root, run_dir)
        services = PipelineServices(
            model=local_model(args.endpoint, model_id=plan.config.model_id),
            tokenizer=OfficialTokenizer.load(
                cache_dir=runtime / "hf",
                local_files_only=True,
                model_id=plan.config.model_id,
                revision=plan.config.model_revision,
            ),
            sandbox_factory=lambda task, environment: DockerSandbox(
                task,
                environment,
                image=plan.config.sandbox_image,
                test_timeout=plan.config.test_timeout_seconds,
            ),
        )
        if args.command == "collect":
            result = asyncio.run(collect_study(root, run_dir, services))
        else:
            result = asyncio.run(
                report_study(
                    root,
                    run_dir,
                    services,
                    audit_path=_path(root, args.summary_audit) if args.summary_audit else None,
                )
            )
        print(f"{result.phase}: {result.completed} completed; {len(result.failures)} failures")
        for failure in result.failures:
            print(
                f"{failure.stage} {failure.history_id} {failure.arm or ''}: "
                f"{failure.error}; log: {failure.log_path}",
                file=sys.stderr,
            )
        return int(bool(result.failures))
    except (OSError, ValueError, SandboxError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
