# -*- coding: utf-8 -*-
"""Data collection layer for Scopos.

This module is intentionally free of any UI code so it can be reused or
tested on its own. :class:`Monitor` keeps a small amount of state between
refreshes so that a given user always keeps the same colour and the same
process numbering, exactly like the original CLI did.
"""

from __future__ import annotations
import re
import time
import random
from dataclasses import (dataclass, field)
from typing import (Dict, List, Optional)

try:  # pynvml is only available on machines with an NVIDIA driver.
    import pynvml as pn
except Exception:  # pragma: no cover - exercised only without a driver.
    pn = None

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


# A palette of visually distinct colours assigned to users in order of
# first appearance. Names are Rich/Textual colour names so they render the
# same in tables, bars and legends.
USER_PALETTE: List[str] = [
    # "bright_red",
    "bright_green",
    "bright_yellow",
    "bright_blue",
    "bright_magenta",
    "bright_cyan",
    "orange1",
    "spring_green2",
    "deep_pink2",
    "gold1",
    "dodger_blue1",
    "medium_purple1",
    "chartreuse2",
    "hot_pink",
]


@dataclass
class ProcInfo:
    """A single compute process running on a GPU."""

    pid: int
    name: str
    user: str
    mem: int  # bytes of GPU memory used
    started: str  # parent-process creation time, formatted
    runtime: str  # how long this process has been running, formatted
    number: str  # per-user "parentNo-childNo" label, filled by Monitor
    detail: str  # script/task detail (only filled for the watched user)
    cmd: str = ""  # full command line, including arguments
    ppid: int = 0  # parent pid, used for per-user numbering
    started_ts: float = 0.0  # raw parent start time, for sorting
    runtime_sec: int = 0  # raw runtime in seconds, for sorting


@dataclass
class GPUInfo:
    """A snapshot of one GPU and the processes running on it."""

    index: int
    name: str
    mem_used: int
    mem_total: int
    mem_free: int
    util: int  # core utilisation %, -1 if unknown
    temperature: int  # degrees C, -1 if unknown
    procs: List[ProcInfo] = field(default_factory=list)
    user_mems: Dict[str, int] = field(default_factory=dict)

    @property
    def idle_rate(self) -> float:
        return self.mem_free / self.mem_total if self.mem_total else 0.0

    @property
    def used_rate(self) -> float:
        return self.mem_used / self.mem_total if self.mem_total else 0.0

    def mvp(self) -> Optional[str]:
        """Return the user holding the most memory on this GPU, if any."""
        if not self.user_mems:
            return None
        return max(self.user_mems.items(), key=lambda kv: kv[1])[0]


def fmt_gb(num_bytes: float) -> str:
    return "%.2f" % (num_bytes / (1024 ** 3))


def fmt_duration(seconds: int) -> str:
    """Format a time span with unit symbols, e.g. "2d 03h", "3h 20m", "45s"."""
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h:02d}h"
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


class Monitor:
    """Collects GPU snapshots, keeping per-user state stable across refreshes."""

    def __init__(self, watch_user: str = "", demo: bool = False):
        self.watch_user = watch_user.strip()
        self.demo = demo
        # username -> colour, assigned on first sight and kept forever.
        self._user_colors: Dict[str, str] = {}
        self._next_color = 0
        self._initialised = False
        if self.watch_user:
            # Make sure the watched user always gets `bright_red`.
            self._user_colors.setdefault(self.watch_user, "bright_red")

    # -- colours -----------------------------------------------------------
    def color_for(self, user: str) -> str:
        if user not in self._user_colors:
            color = USER_PALETTE[self._next_color % len(USER_PALETTE)]
            self._user_colors[user] = color
            self._next_color += 1
        return self._user_colors[user]

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if not self.demo and not self._initialised and pn is not None:
            pn.nvmlInit()
            self._initialised = True

    def stop(self):
        if not self.demo and self._initialised and pn is not None:
            try:
                pn.nvmlShutdown()
            except Exception:
                pass
            self._initialised = False

    # -- system memory -----------------------------------------------------
    def system_stats(self) -> Dict[str, tuple]:
        """Return host RAM/swap usage as {"mem": (used, total), "swap": (...)}.

        Falls back to plausible synthetic values when psutil is unavailable
        (e.g. demo mode on a machine without it installed).
        """
        if psutil is not None:
            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            return {"mem": (vm.used, vm.total), "swap": (sm.used, sm.total)}
        rng = random.Random()
        mem_total = 32 * 1024 ** 3
        swap_total = 8 * 1024 ** 3
        return {
            "mem": (int(mem_total * rng.uniform(0.2, 0.8)), mem_total),
            "swap": (int(swap_total * rng.uniform(0.0, 0.4)), swap_total),
        }

    # -- collection --------------------------------------------------------
    def collect(self) -> List[GPUInfo]:
        if self.demo:
            return self._collect_demo()
        return self._collect_real()

    # -- real collection ---------------------------------------------------
    def _collect_real(self) -> List[GPUInfo]:
        self.start()
        gpus: List[GPUInfo] = []

        for gpu_id in range(pn.nvmlDeviceGetCount()):
            handle = pn.nvmlDeviceGetHandleByIndex(gpu_id)
            name = _decode(pn.nvmlDeviceGetName(handle))
            mem = pn.nvmlDeviceGetMemoryInfo(handle)
            used, free = int(mem.used), int(mem.free)
            total = used + free
            util, temp = -1, -1
            try:
                util = pn.nvmlDeviceGetUtilizationRates(handle).gpu
            except Exception:
                pass
            try:
                temp = pn.nvmlDeviceGetTemperature(handle, pn.NVML_TEMPERATURE_GPU)
            except Exception:
                pass

            gpu = GPUInfo(gpu_id, name, used, total, free, util, temp)

            try:
                processes = pn.nvmlDeviceGetComputeRunningProcesses_v2(handle)
            except Exception:
                processes = []

            for process in processes:
                info = self._build_proc(process)
                if info is None:
                    continue
                gpu.procs.append(info)
                gpu.user_mems[info.user] = (
                    gpu.user_mems.get(info.user, 0) + info.mem
                )
            gpus.append(gpu)
        self._assign_numbers(gpus)
        return gpus

    def _assign_numbers(self, gpus: List[GPUInfo]) -> None:
        """Fill in each process's "parentNo-childNo" label.

        Numbering is per user and spans every GPU: a user's parent processes
        are numbered in the order they are first seen across all GPUs, and the
        child counter increments for every process sharing that parent.
        """
        user_ppids: Dict[str, List[int]] = {}
        child_count: Dict[tuple, int] = {}
        for gpu in gpus:
            for proc in gpu.procs:
                ppids = user_ppids.setdefault(proc.user, [])
                if proc.ppid not in ppids:
                    ppids.append(proc.ppid)
                pp_no = ppids.index(proc.ppid) + 1
                key = (proc.user, proc.ppid)
                child_count[key] = child_count.get(key, 0) + 1
                proc.number = f"{pp_no:02d}-{child_count[key]:02d}"

    def _build_proc(self, process) -> Optional[ProcInfo]:
        try:
            pid = int(process.pid)
            p = psutil.Process(pid)
        except Exception:
            return None
        started_ts = 0.0
        try:
            ppid = p.ppid()
            pp = psutil.Process(ppid)
            started_ts = pp.create_time()
            started = time.strftime("%y-%m-%d %H:%M:%S", time.localtime(started_ts))
        except Exception:
            ppid = 0
            pp = None
            started = "?"

        runtime_sec = int(time.time() - p.create_time())
        runtime = fmt_duration(runtime_sec)
        try:
            user = p.username()
        except Exception:
            user = "?"
        self.color_for(user)

        try:
            cmd = " ".join(p.cmdline()).strip()
        except Exception:
            cmd = ""
        if not cmd:
            cmd = p.name()

        mem = int(process.usedGpuMemory or 0)

        detail = "-"
        if user == self.watch_user and pp is not None:
            detail = self._script_detail(p, pp)

        # number is assigned later, once every GPU has been collected.
        return ProcInfo(
            pid, p.name(), user, mem, started, runtime, "", detail,
            cmd=cmd, ppid=ppid, started_ts=started_ts, runtime_sec=runtime_sec,
        )

    def _script_detail(self, p, pp) -> str:
        """Best-effort reconstruction of which task in a shell script is running.

        Ported from the original tool; wrapped so any failure simply shows "?".
        """
        try:
            pp_file_path = pp.open_files()[0].path
            pp_file_name = pp_file_path.rsplit("/", maxsplit=1)[-1]
            cur_cmd = " ".join(p.cmdline())
            total_task = 0
            cur_task = -1
            bash_args: Dict[str, str] = {}

            def replace_bash_args(cmd: str) -> str:
                for arg, val in bash_args.items():
                    rx = re.compile(r"\$(\{" + arg + r"\}|" + arg + r"(?!_))")
                    cmd = rx.sub(val, cmd)
                return cmd.replace('"', "")

            with open(pp_file_path, "r", newline=None) as fh:
                for cmd in fh:
                    if cmd.startswith("#"):
                        continue
                    cmd = cmd.strip("\n")
                    if cmd.startswith(p.name()):
                        total_task += 1
                        if replace_bash_args(cmd) == cur_cmd:
                            cur_task = total_task
                    elif "=" in cmd:
                        key, raw = cmd.split("=", maxsplit=1)
                        if "$" in raw:
                            val = replace_bash_args(raw)
                            if "$" in val:
                                raise NotImplementedError
                        else:
                            val = raw
                        bash_args[key] = val.strip('"')
            return f"{pp_file_name} [{cur_task}/{total_task}]"
        except Exception:
            return "?"

    # -- demo collection ---------------------------------------------------
    def _collect_demo(self) -> List[GPUInfo]:
        rng = random.Random()  # fresh randomness each tick for a "live" feel
        names = [
            "NVIDIA GeForce RTX 4090",
            "NVIDIA A100-SXM4-80GB",
            "NVIDIA H100 80GB HBM3",
        ]
        users_pool = ["alice", "bob", "carol", "dave", "erin", self.watch_user or "frank"]
        # Give each user a small pool of "parent" pids so the same parent can
        # show up on several GPUs - that is what makes the per-user numbering
        # (parentNo-childNo) interesting to look at.
        parent_pids = {u: [rng.randint(1000, 9999) for _ in range(2)] for u in users_pool}
        gpus: List[GPUInfo] = []
        n_gpu = 4
        for gpu_id in range(n_gpu):
            total = rng.choice([24, 40, 80]) * 1024 ** 3
            gpu = GPUInfo(
                gpu_id,
                names[gpu_id % len(names)],
                0,
                total,
                total,
                rng.randint(0, 100),
                rng.randint(35, 85),
            )
            used = 0
            n_proc = rng.randint(0, 5)
            for _ in range(n_proc):
                user = rng.choice(users_pool)
                self.color_for(user)
                mem = rng.randint(1, 12) * 1024 ** 3
                if used + mem > total:
                    break
                used += mem
                detail = "-"
                if user == self.watch_user:
                    detail = f"train_{rng.randint(1,9)}.sh [{rng.randint(1,4)}/4]"
                runtime_sec = rng.randint(0, 400000)
                started_ts = time.time() - runtime_sec
                script = rng.choice(["train.py", "finetune.py", "eval.py", "main.py"])
                cmd = (
                    f"python {script} --lr {rng.choice(['1e-3', '5e-4', '3e-5'])}"
                    f" --batch-size {rng.choice([16, 32, 64])}"
                    f" --epochs {rng.randint(10, 200)} --fp16"
                )
                gpu.procs.append(
                    ProcInfo(
                        pid=rng.randint(10000, 99999),
                        name=rng.choice(["python", "python3", "train", "pt_main"]),
                        user=user,
                        mem=mem,
                        started=time.strftime("%y-%m-%d %H:%M:%S", time.localtime(started_ts)),
                        runtime=fmt_duration(runtime_sec),
                        number="",
                        detail=detail,
                        cmd=cmd,
                        ppid=rng.choice(parent_pids[user]),
                        started_ts=started_ts,
                        runtime_sec=runtime_sec,
                    )
                )
                gpu.user_mems[user] = gpu.user_mems.get(user, 0) + mem
            gpu.mem_used = used
            gpu.mem_free = total - used
            gpus.append(gpu)
        self._assign_numbers(gpus)
        return gpus


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
