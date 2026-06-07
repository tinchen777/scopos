# -*- coding: utf-8 -*-
"""Data collection layer for Scopos.

This module is intentionally free of any UI code so it can be reused or
tested on its own. :class:`Monitor` keeps a small amount of state between
refreshes so that a given user always keeps the same colour and the same
process numbering, exactly like the original CLI did.
"""

from __future__ import annotations
import pynvml as pn
import psutil
import time
import random
import subprocess
from dataclasses import (dataclass, field)
from typing import (Any, Dict, List, Tuple, Optional)

from .metadata.utils import (make_progress, read_fields, is_progress, iter_pids)

# A palette of visually distinct colours assigned to users in order of
# first appearance. Names are Rich/Textual colour names so they render the
# same in tables, bars and legends.
USER_PALETTE: List[str] = [
    "bright_green",
    "bright_yellow",
    # "bright_blue",
    "bright_magenta",
    "bright_red",
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
    pname: str
    user: str
    mem: int  # bytes of GPU memory used
    runtime: str  # how long this process has been running, formatted
    cmd: str  # full command line, including arguments
    runtime_sec: int  # raw runtime in seconds, for sorting

    sid: int  # session id, i.e. the top-level parent process pid
    sname: str  # session name, i.e. the top-level parent process name
    s_start: str  # session start time, formatted
    s_start_ts: float  # raw session start time, for sorting

    number: str = ""  # per-user "parentNo-childNo" label, filled by Monitor

    # Free-form fields this process reported through ``scopos.report(...)``,
    # read back from ~/.scopos/metadata/<pid>.json. Shown in zen mode.
    meta: Dict[str, Any] = field(default_factory=dict)


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


class _HostProc:
    """Minimal stand-in for an NVML process record, for non-GPU processes.

    It exposes the same ``pid`` / ``usedGpuMemory`` attributes that
    :meth:`Monitor._build_proc` reads, but reports the process's host RSS as
    its "memory" so the PENDING card's MEM column shows something useful while
    a job is still loading, before it touches the GPU.
    """

    def __init__(self, pid: int):
        self.pid = pid
        try:
            self.usedGpuMemory = int(psutil.Process(pid).memory_info().rss)
        except Exception:
            self.usedGpuMemory = 0


class Monitor:
    """Collects GPU snapshots, keeping per-user state stable across refreshes."""

    def __init__(self, watch_user: str = ""):
        self.watch_user = watch_user.strip()
        # username -> colour, assigned on first sight and kept forever.
        self._user_colors: Dict[str, str] = {}
        self._next_color = 0
        self._initialised = False
        # (pid, field) -> (t0, value0): when a progress bar was first seen and
        # at what value, so we can estimate a time-to-completion across refreshes.
        self._prog_hist: Dict[Tuple[int, str], Tuple[float, float]] = {}
        # pane_pid -> tmux session name, rebuilt each ``collect`` cycle.
        self._tmux_panes: Dict[int, str] = {}
        if self.watch_user:
            # Make sure the watched user always gets `bright_red`.
            self._user_colors.setdefault(self.watch_user, "bright_blue")

    # -- colours -----------------------------------------------------------
    def color_for(self, user: str) -> str:
        if user not in self._user_colors:
            color = USER_PALETTE[self._next_color % len(USER_PALETTE)]
            self._user_colors[user] = color
            self._next_color += 1
        return self._user_colors[user]

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if not self._initialised:
            pn.nvmlInit()
            self._initialised = True

    def stop(self):
        if self._initialised:
            try:
                pn.nvmlShutdown()
            except Exception:
                pass
            self._initialised = False

    # -- system memory -----------------------------------------------------
    def system_stats(self) -> Dict[str, tuple]:
        """Return host RAM/swap usage as {"mem": (used, total), "swap": (...)}."""
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()

        return {"mem": (vm.used, vm.total), "swap": (sm.used, sm.total)}

    # -- collection ---------------------------------------------------
    def collect(self) -> List[GPUInfo]:
        self.start()
        self._tmux_panes = self._tmux_pane_map()
        gpus: List[GPUInfo] = []

        for gpu_id in range(pn.nvmlDeviceGetCount()):
            handle = pn.nvmlDeviceGetHandleByIndex(gpu_id)
            name = _decode(pn.nvmlDeviceGetName(handle))
            mem = pn.nvmlDeviceGetMemoryInfo(handle)
            used, free = int(mem.used), int(mem.free)
            total = used + free
            try:
                util = int(pn.nvmlDeviceGetUtilizationRates(handle).gpu)
            except Exception:
                util = -1
            try:
                temp = int(pn.nvmlDeviceGetTemperature(handle, pn.NVML_TEMPERATURE_GPU))
            except Exception:
                temp = -1

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
        self._annotate_eta([p for g in gpus for p in g.procs])
        return gpus

    # -- progress ETA ------------------------------------------------------
    def _annotate_eta(self, procs: List[ProcInfo]):
        """Estimate a time-to-completion for every determinate progress bar.

        A progress value carries no timing of its own, so we remember when each
        ``(pid, field)`` bar was first seen and at what fraction, then project
        the remaining time from the rate of progress since then.  The estimate
        is written back into the progress dict as ``"eta"`` (seconds) for the
        renderer to show; it resets if the bar ever moves backwards (a restart).
        """
        now = time.time()
        live = set()
        for proc in procs:
            for key, value in proc.meta.items():
                if not is_progress(value):
                    continue
                frac = value.get("value")
                if frac is None:  # indeterminate bars have no ETA
                    continue
                hkey = (proc.pid, key)
                live.add(hkey)
                prev = self._prog_hist.get(hkey)
                if prev is None or frac < prev[1] - 1e-9:
                    # First sighting, or progress went backwards: (re)start the clock.
                    self._prog_hist[hkey] = (now, frac)
                    continue
                if frac >= 1.0:
                    value["eta"] = 0.0
                    continue
                t0, v0 = prev
                dv, dt = frac - v0, now - t0
                if dv > 0 and dt > 0:
                    value["eta"] = (1.0 - frac) / (dv / dt)
        # Forget bars whose process (or field) is gone, so the map can't grow
        # without bound on a long-running monitor.
        for hkey in [k for k in self._prog_hist if k not in live]:
            del self._prog_hist[hkey]

    # -- pending (not-yet-on-GPU) processes --------------------------------
    def collect_pending(self, gpu_pids: set) -> List[ProcInfo]:
        """Processes that reported to Scopos but haven't allocated GPU memory yet.

        These are discovered purely from the metadata files under
        ``~/.scopos/metadata``, independent of NVML, so a job shows up while it
        is still loading data / importing CUDA, before it appears on any GPU.
        Filtered to the watched user (when set) and to live processes that are
        not already listed on a GPU.
        """
        pending: List[ProcInfo] = []
        for pid in iter_pids():
            if pid in gpu_pids:
                continue
            info = self._build_proc(_HostProc(pid))
            if info is None:
                continue
            if self.watch_user and info.user != self.watch_user:
                continue
            if not info.meta:  # only show processes that actually reported something
                continue
            pending.append(info)
        self._annotate_eta(pending)
        synthetic = GPUInfo(-1, "", 0, 0, 0, -1, -1, procs=pending)
        for p in pending:
            synthetic.user_mems[p.user] = synthetic.user_mems.get(p.user, 0) + p.mem
        self._assign_numbers([synthetic])
        return pending

    def _assign_numbers(self, gpus: List[GPUInfo]):
        """Fill in each process's "parentNo-childNo" label.

        Numbering is per user and spans every GPU: a user's parent processes
        are numbered in the order they are first seen across all GPUs, and the
        child counter increments for every process sharing that parent.
        """
        user_sids: Dict[str, List[int]] = {}
        child_count: Dict[Tuple[str, int], int] = {}
        for gpu in gpus:
            for proc in gpu.procs:
                sids = user_sids.setdefault(proc.user, [])
                if proc.sid not in sids:
                    sids.append(proc.sid)
                s_no = sids.index(proc.sid) + 1
                if proc.sid == proc.pid:
                    proc.number = f"{s_no:02d}"
                else:
                    key = (proc.user, proc.sid)
                    child_count[key] = child_count.get(key, 0) + 1
                    proc.number = f"{s_no:02d}-{child_count[key]:02d}"

    def _build_proc(self, process) -> Optional[ProcInfo]:
        # pid
        try:
            pid = int(process.pid)
            p = psutil.Process(pid)
        except Exception:
            return None
        # name
        name = p.name()
        # user
        try:
            user = p.username()
        except Exception:
            user = "?"
        self.color_for(user)
        # mem
        mem = int(process.usedGpuMemory or 0)
        # cmd
        try:
            cmd = " ".join(p.cmdline()).strip()
        except Exception:
            cmd = name
        # runtime
        runtime_sec = int(time.time() - p.create_time())
        runtime = fmt_duration(runtime_sec)
        # session: walk up to the process whose parent is PID 1, recording the
        # ancestor chain on the way (needed to map tmux panes to sessions).
        chain: List[psutil.Process] = []
        node: Optional[psutil.Process] = p
        seen = set()
        while node is not None and node.pid not in seen:
            seen.add(node.pid)
            chain.append(node)
            try:
                pp = node.parent()
            except Exception:
                pp = None
            if pp is None or pp.pid == 1:
                break
            node = pp
        session = chain[-1]
        sname = session.name()
        sid = session.pid
        s_proc = session
        # For a tmux-managed process the top-level "session" is the shared tmux
        # *server*; the meaningful unit is the pane, so resolve its session name
        # and treat the pane's shell as the session instead.
        if "tmux" in sname.lower():
            pane = self._tmux_session_for(chain)
            if pane is not None:
                pane_pid, tname = pane
                sname = f"tmux:{tname}" if tname else "tmux"
                sid = pane_pid
                try:
                    s_proc = psutil.Process(pane_pid)
                except Exception:
                    s_proc = session
        # session_start_time
        s_start_ts = s_proc.create_time()
        s_start = time.strftime("%y-%m-%d %H:%M:%S", time.localtime(s_start_ts))

        # Fields this process reported to Scopos, if any.
        meta = read_fields(pid)

        # number is assigned later, once every GPU has been collected.
        return ProcInfo(pid=pid, pname=name, user=user, mem=mem, runtime=runtime, cmd=cmd, runtime_sec=runtime_sec, sname=sname, sid=sid, s_start=s_start, s_start_ts=s_start_ts, meta=meta)

    # -- tmux --------------------------------------------------------------
    def _tmux_pane_map(self) -> Dict[int, str]:
        """Map each tmux pane's shell PID to its session name.

        Only the *running user's* tmux server is reachable (its socket lives in
        a per-user, 0700 directory), so panes belonging to other users can't be
        named and fall back to a plain "tmux".  Returns ``{}`` when tmux is not
        installed or no server is running.
        """
        try:
            out = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=1, check=False,
            )
        except Exception:
            return {}
        mapping: Dict[int, str] = {}
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            head, _, name = line.partition(" ")
            try:
                mapping[int(head)] = name.strip()
            except ValueError:
                pass
        return mapping

    def _tmux_session_for(self, chain) -> Optional[Tuple[int, str]]:
        """Find the tmux pane owning a process, given its ancestor chain.

        Returns the deepest ancestor that is a known pane shell (i.e. the
        process's own pane) as ``(pane_pid, session_name)``, or ``None``.
        """
        if not self._tmux_panes:
            return None
        for node in chain:
            name = self._tmux_panes.get(node.pid)
            if name is not None:
                return node.pid, name
        return None


class DemoMonitor(Monitor):
    """Demo monitor for testing purposes."""

    def __init__(self, watch_user: str = ""):
        super().__init__(watch_user=watch_user)

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        pass

    def stop(self):
        pass

    def _demo_meta(self, rng: random.Random, script: str) -> Dict[str, Any]:
        """Fabricate the kind of fields a script would report via the API.

        Mirrors what :mod:`scopos.api` would write, so zen mode (``z``) has
        something to show - including animated progress bars - in ``--demo``.
        """
        # A few processes report nothing, like real-world jobs that don't use
        # the API; this keeps the demo honest about missing metadata.
        if rng.random() < 0.25:
            return {}
        stage = rng.choice(["warmup", "train", "train", "eval"])
        if stage == "warmup":
            # Indeterminate bar -> animated marquee in the TUI.
            return {"stage": stage, "progress": make_progress(label="loading data")}
        total = rng.choice([50, 100, 200])
        done = rng.randint(0, total)
        meta: Dict[str, Any] = {
            "stage": stage,
            "task": script.replace(".py", ""),
            "epoch": make_progress(done, total),
            "loss": f"{rng.uniform(0.05, 2.5):.4f}",
        }
        if stage == "eval":
            meta["acc"] = f"{rng.uniform(60, 99):.1f}%"
        return meta

    # -- demo collection ---------------------------------------------------
    def collect(self) -> List[GPUInfo]:
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
                runtime_sec = rng.randint(0, 400000)
                s_start_ts = time.time() - runtime_sec
                s_start = time.strftime("%y-%m-%d %H:%M:%S", time.localtime(s_start_ts))
                script = rng.choice(["train.py", "finetune.py", "eval.py", "main.py"])
                cmd = (
                    f"python {script} --lr {rng.choice(['1e-3', '5e-4', '3e-5'])}"
                    f" --batch-size {rng.choice([16, 32, 64])}"
                    f" --epochs {rng.randint(10, 200)} --fp16"
                )
                sid = rng.choice(parent_pids[user])
                gpu.procs.append(
                    ProcInfo(
                        pid=rng.randint(10000, 99999),
                        pname=rng.choice(["python", "python3", "train", "pt_main"]),
                        user=user,
                        mem=mem,
                        runtime=fmt_duration(runtime_sec),
                        cmd=cmd,
                        runtime_sec=runtime_sec,
                        sid=sid,
                        sname=rng.choice(["bash", "zsh", "sbatch", "tmux:main", "tmux:exp1", "tmux:train"]),
                        s_start=s_start,
                        s_start_ts=s_start_ts,
                        number="",
                        meta=self._demo_meta(rng, script),
                    )
                )
                gpu.user_mems[user] = gpu.user_mems.get(user, 0) + mem
            gpu.mem_used = used
            gpu.mem_free = total - used
            gpus.append(gpu)
        self._assign_numbers(gpus)
        self._annotate_eta([p for g in gpus for p in g.procs])
        return gpus

    def collect_pending(self, gpu_pids: set) -> List[ProcInfo]:
        """Fabricate a couple of "still loading" processes for the watched user.

        Lets ``--demo --zen`` show the PENDING card (jobs that have reported to
        Scopos but haven't allocated GPU memory yet).
        """
        rng = random.Random()
        user = self.watch_user or "frank"
        self.color_for(user)
        pending: List[ProcInfo] = []
        for _ in range(rng.randint(0, 2)):
            runtime_sec = rng.randint(1, 120)
            s_start_ts = time.time() - runtime_sec
            pending.append(
                ProcInfo(
                    pid=rng.randint(10000, 99999),
                    pname="python",
                    user=user,
                    mem=rng.randint(1, 8) * 1024 ** 3,  # stands in for host RSS
                    runtime=fmt_duration(runtime_sec),
                    cmd="python train.py --epochs 100 --fp16",
                    runtime_sec=runtime_sec,
                    sid=rng.randint(1000, 9999),
                    sname=rng.choice(["bash", "tmux:train"]),
                    s_start=time.strftime("%y-%m-%d %H:%M:%S", time.localtime(s_start_ts)),
                    s_start_ts=s_start_ts,
                    number="",
                    meta={"stage": "warmup", "epoch": make_progress(label="loading data")},
                )
            )
        synthetic = GPUInfo(-1, "", 0, 0, 0, -1, -1, procs=pending)
        self._assign_numbers([synthetic])
        return pending


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
