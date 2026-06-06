<div align="center">

<h2 id="title">
🐱‍👓 SCOPOS 🐱‍👓<br>
<sub>NVIDIA GPU Monitor</sub>
</h2>

[![PyPI version](https://img.shields.io/pypi/v/scopos.svg)](https://pypi.org/project/scopos/)
![Python](https://img.shields.io/pypi/pyversions/scopos?color=brightgreen)
![License](https://img.shields.io/github/license/tinchen777/scopos.svg)

![Github stars](https://img.shields.io/github/stars/tinchen777/scopos.svg)

</div>

```text
  ___   ___  _____  ____  _____  ___
 / __) / __)(  _  )(  _ \(  _  )/ __)
 \__ \( (__  )(_)(  )___/ )(_)( \__ \
 (___/ \___)(_____)(__)  (_____)(___/
```

## About

Monitor NVIDIA GPU memory usage from the terminal, **grouped by user**. SCOPOS
is built with [Textual](https://textual.textualize.io/): the layout adapts to
your terminal size, and every GPU shows an at-a-glance bar of how its memory is
split between users.

- Python: 3.8+

## Installation

### Install with pipx

`pipx` installs the application in an isolated environment while making
the command globally available.

```bash
pip install pipx
pipx ensurepath
```

```bash
pipx install scopos
```

## Quick Start

### monitor all GPUs

```bash
scopos
```

### highlight user "alice" and show their task details

```bash
scopos -u alice
```

### refresh every 2 seconds

```bash
scopos -i 2
```

### synthetic data, no NVIDIA driver needed

```bash
scopos --demo
```

### start in zen (focus) mode

```bash
scopos -u alice --zen
```

---

## Zen mode

Press <kbd>z</kbd> at any time (or start with `--zen`) to toggle **zen mode**, a
focused layout meant to be paired with `-u/--user`:

- Each GPU's **table** lists only the watched user's processes.
- The per-GPU **bar and legend still show every user** — the watched user is
  highlighted (`★`, bold) so you keep the full picture at a glance.
- The table drops the `USER` and `S.START` columns and instead shows the
  **live fields each process reports** through the Python API below — including
  animated progress bars.

## Python API

`scopos` doubles as a tiny library so your scripts can push live status to the
monitor. Importing it is cheap — no Textual or NVIDIA driver required.

```python
import scopos

# Report plain fields (merged into this process's metadata):
scopos.report(stage="train", loss=0.1234, acc="92.5%")

# Report a progress bar. scopos renders it as a live bar in zen mode:
for step in range(total_steps):
    scopos.report(progress=scopos.progress(step, total_steps))  # e.g. 37/100
    ...

# A fraction in [0, 1] works too, and an indeterminate (animated) bar:
scopos.report(loading=scopos.progress())            # bouncing "…"
scopos.report(warmup=scopos.progress(0.5, label="halfway"))

# Drop a field by reporting None; replace everything with set(...):
scopos.report(loss=None)
scopos.set(stage="done")

# Or scope a run and clean up automatically:
with scopos.session(stage="train"):
    train()   # metadata file removed on exit
```

Each process writes `~/.scopos/metadata/<pid>.json`; `scopos` reads it back and,
in zen mode, shows every reported field as a column next to that process. The
file is removed automatically when your program exits (`atexit`). Set
`$SCOPOS_HOME` to relocate the `.scopos` directory.

---

## Requirements

- Python >= 3.8
- `textual` >= 0.60
- `psutil` >= 5.9
- `nvidia-ml-py` >= 12.0

## License

See LICENSE in the repository.

## Links

- [Homepage/Repo](https://github.com/tinchen777/scopos.git)
- [Issues](https://github.com/tinchen777/scopos.git/issues)
