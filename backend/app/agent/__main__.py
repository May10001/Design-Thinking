"""Run either adapter operation from a JSON request file."""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from pydantic import ValidationError

from ..adapters.agent_adapter import AgentAdapter, AgentError
from ..schemas.agent import ExtractionRequest, MatchBatchRequest


async def run(operation: str, request_path: Path) -> None:
    model = ExtractionRequest if operation == "extract" else MatchBatchRequest
    request = model.model_validate_json(request_path.read_bytes(), strict=True)
    async with AgentAdapter() as agent:
        if isinstance(request, ExtractionRequest):
            result = await agent.extract_tasks(request)
        else:
            result = await agent.match_tasks(request)
    print(result.model_dump_json(indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(prog="deadline_keeper", description="Extract and match tasks")
    parser.add_argument("operation", choices=["extract", "match"])
    parser.add_argument("request", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    load_dotenv(args.env_file)
    try:
        asyncio.run(run(args.operation, args.request))
    except AgentError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, ValidationError):
        print("INVALID_INPUT: Check request path and JSON fields", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
