"""Temporal worker and a small client for the hello-world workflow.

python -m app.worker worker          run the worker until stopped
python -m app.worker hello NAME      start HelloWorkflow and print the result
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from temporalio.client import Client
from temporalio.worker import Worker

from app.settings import Settings, get_settings
from app.workflows import ACTIVITIES, WORKFLOWS, HelloInput, HelloResult, HelloWorkflow


async def connect(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


def build_worker(client: Client, task_queue: str) -> Worker:
    return Worker(client, task_queue=task_queue, workflows=WORKFLOWS, activities=ACTIVITIES)


async def run_worker(settings: Settings) -> None:
    client = await connect(settings)
    await build_worker(client, settings.temporal_task_queue).run()


async def start_hello(client: Client, task_queue: str, name: str) -> HelloResult:
    return await client.execute_workflow(
        HelloWorkflow.run,
        HelloInput(name=name),
        id=f"hello-{uuid.uuid4()}",
        task_queue=task_queue,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("worker")
    hello = sub.add_parser("hello")
    hello.add_argument("name")
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "worker":
        asyncio.run(run_worker(settings))
        return 0

    async def _hello() -> HelloResult:
        client = await connect(settings)
        return await start_hello(client, settings.temporal_task_queue, args.name)

    result = asyncio.run(_hello())
    print(f"{result.greeting} (config {result.config_version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
