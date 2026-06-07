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
import contextlib
from dataclasses import (dataclass, field)
from typing import (Any, Dict, List, Tuple, Optional)

from . import config
from .metadata.utils import (make_progress, read_fields, is_progress, iter_pids)

# A palette of visually distinct colours assigned to users in order of first
# appearance, so a given user keeps the same colour in tables, bars and legends.
# Sourced from scopos.config so it can be themed/overridden in one place.
USER_PALETTE: List[str] = config.USER_PALETTE


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

    rss: int = 0  # host (CPU) memory used, in bytes

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
    :meth:`Monitor._build_proc` reads.  These processes hold no GPU memory, so
    ``usedGpuMemory`` is 0; their host RAM is filled in from psutil like any
    other process.
    """

    def __init__(self, pid: int):
        self.pid = pid
        self.usedGpuMemory = 0


class Monitor:
    """Collects GPU snapshots, keeping per-user state stable across refreshes."""

    def __init__(self, watch_user: str = ""):
        self.watch_user = watch_user.strip()
        # username -> colour, assigned on first sight and kept forever.
        self._user_colors: Dict[str, str] = {}
        self._next_color = 0
        self._initialised = False
        # (pid, field) -> (t0, value0, last_seen): when a progress bar was first
        # seen and at what value (plus when last updated), so we can estimate a
        # time-to-completion across refreshes and prune stale entries by age.
        self._prog_hist: Dict[Tuple[int, str], Tuple[float, float, float]] = {}
        # pane_pid -> tmux session name, rebuilt each ``collect`` cycle.
        self._tmux_panes: Dict[int, str] = {}
        if self.watch_user:
            # Make sure the watched user always gets `bright_red`.
            self._user_colors.setdefault(self.watch_user, config.WATCH_USER_COLOR)

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

        History is keyed by ``(pid, field)`` and entries record when they were
        last touched.  Pruning is *time-based* (not "anything not in this call"),
        because GPU and CPU processes are annotated in separate passes and must
        not wipe each other's clock.
        """
        now = time.time()
        for proc in procs:
            for key, value in proc.meta.items():
                if not is_progress(value):
                    continue
                frac = value.get("frac")
                if frac is None:  # indeterminate bars have no ETA
                    continue
                hkey = (proc.pid, key)
                prev = self._prog_hist.get(hkey)
                if prev is None or frac < prev[1] - 1e-9:
                    # First sighting, or progress went backwards: (re)start the clock.
                    self._prog_hist[hkey] = (now, frac, now)
                    continue
                t0, v0, _ = prev
                self._prog_hist[hkey] = (t0, v0, now)  # keep it alive
                if frac >= 1.0:
                    value["eta"] = 0.0
                    continue
                dv, dt = frac - v0, now - t0
                if dv > 0 and dt > 0:
                    value["eta"] = (1.0 - frac) / (dv / dt)
        # Drop bars not seen for a while so the map can't grow without bound.
        cutoff = now - 300
        for hkey in [k for k, v in self._prog_hist.items() if v[2] < cutoff]:
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
        # Batch the per-process reads below into a single pass where the OS
        # supports it; cheaper than fetching each attribute on its own.
        try:
            ctx = p.oneshot()
        except Exception:
            ctx = contextlib.nullcontext()
        with ctx:
            name = p.name()
            try:
                user = p.username()
            except Exception:
                user = "?"
            try:
                cmd = " ".join(p.cmdline()).strip()
            except Exception:
                cmd = name
            try:
                runtime_sec = int(time.time() - p.create_time())
            except Exception:
                runtime_sec = 0
            try:
                rss = int(p.memory_info().rss)  # host (CPU) memory
            except Exception:
                rss = 0
        self.color_for(user)
        mem = int(process.usedGpuMemory or 0)  # GPU memory (0 for non-GPU procs)
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
        return ProcInfo(pid=pid, pname=name, user=user, mem=mem, runtime=runtime, cmd=cmd, runtime_sec=runtime_sec, sname=sname, sid=sid, s_start=s_start, s_start_ts=s_start_ts, rss=rss, meta=meta)

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
    """Synthetic monitor with a *stable, evolving* set of processes.

    Unlike a fresh-random-every-tick fake, the jobs here keep their PIDs and
    advance their progress over wall-clock time, so ``--demo`` exercises the
    real features: live progress bars with an ETA, host-RAM columns, and the
    resident CPU card (jobs that report to scopos but never touch a GPU).
    """

    GPU_NAMES = [
        "NVIDIA GeForce RTX 4090",
        "NVIDIA A100-SXM4-80GB",
        "NVIDIA H100 80GB HBM3",
        "NVIDIA A100-SXM4-80GB",
    ]
    GPU_TOTAL_GB = [24, 80, 80, 80]

    def __init__(self, watch_user: str = ""):
        super().__init__(watch_user=watch_user)
        self._jobs: Optional[List[Dict[str, Any]]] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        pass

    def stop(self):
        pass

    # -- synthetic jobs ----------------------------------------------------
    def _ensure_jobs(self):
        """Build the fixed roster of demo jobs once (seeded, so it's stable)."""
        if self._jobs is not None:
            return
        rng = random.Random(42)
        watch = self.watch_user or "frank"
        users = ["alice", "bob", "carol", watch]
        scripts = ["train.py", "finetune.py", "eval.py", "main.py"]
        now = time.time()
        pid = 10001
        jobs: List[Dict[str, Any]] = []

        def make(user: str, gpu: Optional[int], kind: Optional[str] = None) -> Dict[str, Any]:
            nonlocal pid
            kind = kind or rng.choice(["determinate", "determinate", "warmup", "none"])
            script = rng.choice(scripts)
            total = rng.choice([50, 100, 200])
            job = {
                "pid": pid,
                "user": user,
                "gpu": gpu,
                "pname": rng.choice(["python", "python3", "pt_main"]),
                "mem": (rng.randint(2, 10) * 1024 ** 3) if gpu is not None else 0,
                "rss": rng.randint(1, 24) * 1024 ** 3,
                "sid": rng.randint(1000, 9999),
                "sname": rng.choice(["bash", "zsh", "sbatch", "tmux:main", "tmux:exp1", "tmux:train"]),
                "start": now - rng.randint(30, 6000),
                "kind": kind,
                "script": script,
                "total": total,
                "duration": rng.uniform(60, 240),  # seconds for one full run
                "loss0": rng.uniform(1.5, 3.0),
            }
            job["cmd"] = (
                f"python {script} --lr {rng.choice(['1e-3', '5e-4', '3e-5'])}"
                f" --batch-size {rng.choice([16, 32, 64])} --epochs {total} --fp16"
            )
            pid += 1
            return job

        for gpu_id in range(4):
            for _ in range(rng.randint(1, 3)):
                jobs.append(make(rng.choice(users), gpu_id))
        # Make sure the watched user has a couple of GPU jobs to focus on.
        jobs.append(make(watch, 0))
        jobs.append(make(watch, 2))
        # CPU-only jobs for the watched user (these populate the CPU card). They
        # always report something, since only API users appear there.
        for _ in range(2):
            jobs.append(make(watch, None, kind=rng.choice(["determinate", "warmup"])))
        self._jobs = jobs

    def _job_meta(self, job: Dict[str, Any]) -> Dict[str, Any]:
        kind = job["kind"]
        if kind == "none":
            return {}
        elapsed = time.time() - job["start"]
        phase = elapsed % job["duration"]  # loops, so a run "restarts" periodically
        if kind == "warmup" and phase < 15:
            # First seconds of each loop: an indeterminate "loading" marquee.
            return {"stage": "warmup", "epoch": make_progress(label="loading data")}
        frac = phase / job["duration"]
        done = int(frac * job["total"])
        return {
            "stage": "train",
            "task": job["script"].replace(".py", ""),
            "epoch": make_progress(done, job["total"]),
            "loss": f"{job['loss0'] * (1 - 0.8 * frac):.4f}",
        }

    def _job_proc(self, job: Dict[str, Any]) -> ProcInfo:
        self.color_for(job["user"])
        runtime_sec = int(time.time() - job["start"])
        return ProcInfo(
            pid=job["pid"], pname=job["pname"], user=job["user"],
            mem=job["mem"], runtime=fmt_duration(runtime_sec), cmd=job["cmd"],
            runtime_sec=runtime_sec, sid=job["sid"], sname=job["sname"],
            s_start=time.strftime("%y-%m-%d %H:%M:%S", time.localtime(job["start"])),
            s_start_ts=job["start"], rss=job["rss"], meta=self._job_meta(job),
        )

    # -- demo collection ---------------------------------------------------
    def collect(self) -> List[GPUInfo]:
        self._ensure_jobs()
        assert self._jobs is not None
        rng = random.Random()  # only util/temperature jitter is random per tick
        gpus: List[GPUInfo] = []
        for gpu_id in range(4):
            total = self.GPU_TOTAL_GB[gpu_id] * 1024 ** 3
            gpus.append(GPUInfo(
                gpu_id, self.GPU_NAMES[gpu_id], 0, total, total,
                rng.randint(0, 100), rng.randint(35, 85),
            ))
        for job in self._jobs:
            if job["gpu"] is None:
                continue
            gpu = gpus[job["gpu"]]
            proc = self._job_proc(job)
            gpu.procs.append(proc)
            gpu.user_mems[proc.user] = gpu.user_mems.get(proc.user, 0) + proc.mem
        for gpu in gpus:
            used = min(sum(p.mem for p in gpu.procs), gpu.mem_total)
            gpu.mem_used = used
            gpu.mem_free = gpu.mem_total - used
        self._assign_numbers(gpus)
        self._annotate_eta([p for g in gpus for p in g.procs])
        return gpus

    def collect_pending(self, gpu_pids: set) -> List[ProcInfo]:
        """The watched user's CPU-only (never-on-GPU) reporting jobs."""
        self._ensure_jobs()
        assert self._jobs is not None
        user = self.watch_user or "frank"
        pending = [
            self._job_proc(job) for job in self._jobs
            if job["gpu"] is None and job["user"] == user
        ]
        synthetic = GPUInfo(-1, "CPU", 0, 0, 0, -1, -1, procs=pending)
        self._assign_numbers([synthetic])
        self._annotate_eta(pending)
        return pending


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
