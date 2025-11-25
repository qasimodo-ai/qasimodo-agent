# QAsimodo agent

## Installation

### Pre-built Binaries

Pre-built executables are available for multiple platforms:
- **Linux** (x86_64): `qasimodo-agent-linux`
- **Windows** (x86_64): `qasimodo-agent-windows` (installer)
- **macOS ARM64**: `qasimodo-agent-macos-arm64`
- **macOS x86_64**: `qasimodo-agent-macos-x86_64`

**Recommended**: Download from the [latest release](https://github.com/qasimodo-ai/qasimodo-agent/releases/latest).

Alternatively, you can download artifacts from the [latest commit](https://github.com/qasimodo-ai/qasimodo-agent/actions/workflows/build.yml) (requires GitHub login).

### Docker

Pull and run the Docker image:
```bash
docker pull ghcr.io/qasimodo-ai/qasimodo-agent:latest
docker run ghcr.io/qasimodo-ai/qasimodo-agent:latest
```

## Usage

TODO

## Agent state

The agent persists local state in `~/.qasimodo-agent/agents.json`. This file keeps a stable agent ID per project alongside metadata (such as the binary version pulled from `pyproject.toml`) and will accumulate additional fields over time.

## Development

We recommend using [Nix](https://nixos.org) with [direnv](https://direnv.net) for development. Once installed, the dev shell will automatically load with all required tools.

When entering the dev shell, pre-commit hooks are automatically installed to verify code formatting. You can also manually format the entire codebase with:
```bash
nix fmt .
```

Build the project:
```bash
uv build
```

Run the agent:
```bash
uv run qasimodo-agent
```

Run the agent with the Rich TUI dashboard:
```bash
QASIMODO_AGENT_LLM_API_KEY=<your_key> \
CHROMIUM_PATH=<path_to_chromium> \
QASIMODO_NATS_URL=nats://nats.qasimodo.com:4222 \
uv run qasimodo-agent-tui
```
The TUI shows agent metadata (status, version, last heartbeat) and a live log feed. Use Ctrl+C to stop.

### Reproducible builds with Nix

Build the package reproducibly:
```bash
nix build .#qasimodo-agent
```

Or run directly without cloning the repository:
```bash
nix run github:qasimodo-ai/qasimodo-agent
```
