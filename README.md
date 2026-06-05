
# SCOPOS

```text
  ___   ___  _____  ____  _____  ___
 / __) / __)(  _  )(  _ \(  _  )/ __)
 \__ \( (__  )(_)(  )___/ )(_)( \__ \
 (___/ \___)(_____)(__)  (_____)(___/ v2.0.0
```

Monitor NVIDIA GPU memory usage from the terminal, **grouped by user**. SCOPOS
is built with [Textual](https://textual.textualize.io/): the layout adapts to
your terminal size, and every GPU shows an at-a-glance bar of how its memory is
split between users.

## Features

- **Adaptive layout** — GPU cards tile into as many columns as your terminal is
  wide, and the whole view scrolls when there are more GPUs than fit.
- **Per-user proportion bar** — each GPU has a single coloured bar showing how
  its memory is divided between users, with a legend and a 🏆 for the biggest
  consumer. Colours are consistent for a user across every GPU.
- **Process table** — PID, process name, user, per-user numbering, memory,
  start time and runtime for every compute process.
- **User watch mode** (`-u`) — highlights one user and reconstructs which task
  of their shell script is currently running.
- **Logo + live clock** pinned to the top of the screen.

## Install

```bash
pip install -e .
# or just the dependencies:
pip install -r requirements.txt
```

## Usage

```bash
scopos                 # monitor all GPUs
scopos -u alice        # highlight user "alice" and show their task details
scopos -i 2            # refresh every 2 seconds
scopos --demo          # synthetic data, no NVIDIA driver needed

# without installing:
python -m scopos --demo
```

Keys: `q` quit · `r` refresh now · `d` toggle light/dark.

## Legacy CLI

The original single-file `print`-based version is preserved as
[`SCOPOS.py`](./SCOPOS.py) for reference.
