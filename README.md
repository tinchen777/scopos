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
