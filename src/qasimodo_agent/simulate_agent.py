import asyncio
import sys
import nats
from textwrap import dedent


async def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: simulate-agent <agent_id>")
        sys.exit(1)

    agent_id = sys.argv[1]

    print(f"Starting agent: {agent_id}")

    # Connect to NATS
    nc = await nats.connect("nats://localhost:4222")
    js = nc.jetstream()

    # Setup stream
    try:
        await js.stream_info("AGENTS")
    except:
        await js.add_stream(name="AGENTS", subjects=["agents.>"])

    # Subscribe to tasks
    task_subject = f"agents.{agent_id}.tasks"
    result_subject = f"agents.{agent_id}.results"

    print(f"Listening on: {task_subject}")
    print(f"Publishing to: {result_subject}\n")

    print(dedent(f'''
        Run the following commands:
        nats sub agents.{agent_id}.results
        nats pub agents.{agent_id}.tasks "test task"
    '''))

    psub = await js.pull_subscribe(task_subject, durable=f"agent-{agent_id}")

    try:
        while True:
            try:
                messages = await psub.fetch(batch=1, timeout=1.0)
                for msg in messages:
                    task = msg.data.decode()
                    print(f"📥 Received task: {task}")

                    # Simulate work (5 seconds)
                    print("⏳ Processing (5s)...")
                    await asyncio.sleep(5)

                    # Publish result
                    result = f"Task '{task}' completed"
                    await js.publish(result_subject, result.encode())
                    print(f"✓ Result published: {result}\n")

                    # Acknowledge
                    await msg.ack()

            except asyncio.TimeoutError:
                pass

    except KeyboardInterrupt:
        print("\n🛑 Stopping agent")
        await nc.close()


def cli():
    """Command-line interface."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
