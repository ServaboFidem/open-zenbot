# Getting Started

After you complete this page, you run the harness and open the app. This page covers install, start, config, and first launch. It does not describe architecture, providers, skills, or MCP servers.

## Before you begin

You need these items before step one:

- A local copy of this repository.
- `uv`.
- `Node.js` with `npm`.

Run every command from the repo root.

## Install host tools

Run this step once.

To install host tools on Windows, run the following command:

```powershell
.\scripts\install-deps.ps1
```

To install host tools on Linux, run the following command:

```sh
./scripts/install-deps.sh
```

`install-deps` accepts `--dry-run` to list actions without running them.

## Start the harness

To start the harness and the app on Windows, run the following command:

```powershell
.\scripts\dev.ps1
```

To start the harness and the app on Linux, run the following command:

```sh
./scripts/dev.sh
```

On the first start, `dev` installs `npm` packages when `node_modules` is missing. Then `dev` builds the GUI. Then `dev` starts the harness at `http://127.0.0.1:8765` and opens the app window. Close the window to stop the harness.

To use another port on Windows, add `-Port 9000`. To use another port on Linux, add `--port 9000`.

## Change a setting

No key is needed to start. The harness starts unbound. Add your OpenRouter key on the Settings page of the app.

To change any other setting, copy `.env.example` to `.env` at the repo root. `.env.example` lists each variable with its default. A variable set in the shell wins over the file. Restart the harness and the app after you edit `.env`.

> [!CAUTION]
> `.env` holds secrets. Never commit it.

## Complete the first launch

The first start downloads memory models for two to four minutes. The app shows a loading overlay during the download. Later starts take about 35 seconds.

## Confirm the result

Confirm that the app window opens. Confirm that the harness answers at `http://127.0.0.1:8765`.

## Next step

Next, read the Capabilities page to learn what bots do after the app opens.
