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
from typing import (Any, Set, Dict, List, Tuple, Optional)

from . import config
from .metadata.utils import (make_progress, read_fields, is_progress, fetch_pids)

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
class DeviceInfo:
    """A snapshot of one GPU or CPU, with its processes and per-user aggregates."""

    name: str
    procs: List[ProcInfo] = field(default_factory=list, init=False)


@dataclass
class CPUInfo(DeviceInfo):
    """A snapshot of one CPU and the processes running on it."""

    user_rsss: Dict[str, int] = field(default_factory=dict, init=False)

    def mvp(self) -> Optional[str]:
        """Return the user holding the most memory on this CPU, if any."""
        if not self.user_rsss:
            return None
        return max(self.user_rsss.items(), key=lambda kv: kv[1])[0]


@dataclass
class GPUInfo(DeviceInfo):
    """A snapshot of one GPU and the processes running on it."""

    id: int
    mem_used: int
    mem_total: int
    mem_free: int
    mem_util: int  # core utilisation %, -1 if unknown
    temperature: int  # degrees C, -1 if unknown
    user_mems: Dict[str, int] = field(default_factory=dict, init=False)

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


@dataclass
class TmuxPane:
    """One tmux pane and the process subtree running inside it.

    ``procs`` is the pane's process subtree, shell first (``procs[0]`` is the
    pane's shell, ``procs[1:]`` the commands running under it).
    """

    session: str
    attached: bool
    window_idx: int
    window_name: str
    pane_idx: int
    pane_pid: int
    procs: List[ProcInfo] = field(default_factory=list)


@dataclass
class TmuxSession:
    """One tmux session: a name, attached flag and its panes."""

    name: str
    attached: bool
    panes: List[TmuxPane] = field(default_factory=list)

    @property
    def all_procs(self) -> List[ProcInfo]:
        return [p for pane in self.panes for p in pane.procs]


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

    def __init__(self, focus_user: str):
        self.focus_user = focus_user.strip()
        # username -> colour, assigned on first sight and kept forever.
        self._user_colors: Dict[str, str] = {}
        self._next_color = 0
        self._pn_initialised = False
        # (pid, field) -> (t0, value0, last_seen): when a progress bar was first
        # seen and at what value (plus when last updated), so we can estimate a
        # time-to-completion across refreshes and prune stale entries by age.
        self._prog_hist: Dict[Tuple[int, str], Tuple[float, float, float]] = {}
        # pane_pid -> tmux session name, rebuilt each ``collect`` cycle.
        self._tmux_panes: Dict[int, str] = {}

        self._user_colors.setdefault(self.focus_user, config.FOCUS_USER_COLOR)

    # -- colours -----------------------------------------------------------
    def color_for(self, user: str) -> str:
        if user not in self._user_colors:
            color = USER_PALETTE[self._next_color % len(USER_PALETTE)]
            self._user_colors[user] = color
            self._next_color += 1
        return self._user_colors[user]

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if not self._pn_initialised:
            pn.nvmlInit()
            self._pn_initialised = True

    def stop(self):
        if self._pn_initialised:
            try:
                pn.nvmlShutdown()
            except Exception:
                pass
            self._pn_initialised = False

    # -- collection ---------------------------------------------------
    def collect_GPU(self) -> Tuple[List[GPUInfo], List[ProcInfo]]:
        self.start()
        self._tmux_panes = self._tmux_pane_map()
        gpus: List[GPUInfo] = []
        gpu_procs: List[ProcInfo] = []

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

            gpu = GPUInfo(id=gpu_id, name=name, mem_used=used, mem_total=total, mem_free=free, mem_util=util, temperature=temp)

            try:
                processes = pn.nvmlDeviceGetComputeRunningProcesses_v2(handle)
            except Exception:
                processes = []

            for process in processes:
                proc = self._build_proc(process)
                if proc is None:
                    continue
                gpu.procs.append(proc)
                gpu.user_mems.setdefault(proc.user, 0)
                gpu.user_mems[proc.user] += proc.mem

                gpu_procs.append(proc)
            gpus.append(gpu)
        self._assign_numbers(gpu_procs)
        self._annotate_eta(gpu_procs)

        return gpus, gpu_procs

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
    def collect_CPU(self, gpu_pids: Set[int]) -> CPUInfo:
        """Processes that reported to Scopos but haven't allocated GPU memory yet.

        These are discovered purely from the metadata files under
        ``~/.scopos/metadata``, independent of NVML, so a job shows up while it
        is still loading data / importing CUDA, before it appears on any GPU.
        Filtered to the watched user (when set) and to live processes that are
        not already listed on a GPU.
        """
        cpu = CPUInfo(name="CPU")

        for pid in fetch_pids():
            if pid in gpu_pids:
                continue
            proc = self._build_proc(_HostProc(pid))
            if proc is None:
                continue
            if proc.user != self.focus_user:
                continue
            # if not proc.meta:  # only show processes that actually reported something
            #     continue
            cpu.procs.append(proc)
            cpu.user_rsss.setdefault(proc.user, 0)
            cpu.user_rsss[proc.user] += proc.rss
        self._annotate_eta(cpu.procs)
        self._assign_numbers(cpu.procs)

        return cpu

    def _assign_numbers(self, procs: List[ProcInfo]):
        """Fill in each process's "parentNo-childNo" label.

        Numbering is per user and spans every GPU: a user's parent processes
        are numbered in the order they are first seen across all GPUs, and the
        child counter increments for every process sharing that parent.
        """
        user_sids: Dict[str, List[int]] = {}
        child_count: Dict[Tuple[str, int], int] = {}
        for proc in procs:
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
    @staticmethod
    def _tmux_pane_map() -> Dict[int, str]:
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

    # -- info --------------------------------------------------------------
    def gpu_specs(self) -> List[Tuple[int, str, int]]:
        """Light GPU inventory (id, name, total bytes) without scanning procs."""
        try:
            self.start()
            specs = []
            for i in range(pn.nvmlDeviceGetCount()):
                handle = pn.nvmlDeviceGetHandleByIndex(i)
                name = _decode(pn.nvmlDeviceGetName(handle))
                mem = pn.nvmlDeviceGetMemoryInfo(handle)
                specs.append((i, name, int(mem.used + mem.free)))
            return specs
        except Exception:
            return []

    # -- tmux mode ---------------------------------------------------------
    def collect_tmux(self) -> List[TmuxSession]:
        """Snapshot the running user's tmux sessions, panes and processes.

        Only the running user's tmux server is reachable (its socket lives in a
        per-user directory), so this shows *your own* tmux. Returns ``[]`` when
        tmux isn't installed or no server is running. (No GPU/NVML needed.)
        """
        self._tmux_panes = self._tmux_pane_map()
        sep = "\t"
        fmt = sep.join((
            "#{session_name}", "#{session_attached}", "#{window_index}",
            "#{window_name}", "#{pane_index}", "#{pane_pid}",
        ))
        try:
            out = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", fmt],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=2, check=False,
            )
        except Exception:
            return []
        sessions: Dict[str, TmuxSession] = {}
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split(sep)
            if len(parts) != 6:
                continue
            sname, attached, widx, wname, pidx, ppid = parts
            try:
                pane_pid = int(ppid)
            except ValueError:
                continue
            is_attached = attached.strip() not in ("", "0")
            session = sessions.get(sname)
            if session is None:
                session = TmuxSession(name=sname, attached=is_attached)
                sessions[sname] = session
            pane = TmuxPane(
                session=sname, attached=is_attached,
                window_idx=_to_int(widx), window_name=wname,
                pane_idx=_to_int(pidx), pane_pid=pane_pid,
                procs=self._pane_procs(pane_pid),
            )
            session.panes.append(pane)
        all_procs = [p for s in sessions.values() for p in s.all_procs]
        self._assign_numbers(all_procs)
        self._annotate_eta(all_procs)
        return list(sessions.values())

    def _pane_procs(self, pane_pid: int) -> List[ProcInfo]:
        """Build ProcInfo for a pane's process subtree, shell (pane_pid) first."""
        try:
            root = psutil.Process(pane_pid)
            members = [root] + root.children(recursive=True)
        except Exception:
            return []
        procs: List[ProcInfo] = []
        for member in members:
            proc = self._build_proc(_HostProc(member.pid))
            if proc is not None:
                procs.append(proc)
        return procs


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

    def __init__(self, focus_user: str = ""):
        super().__init__(focus_user=focus_user)
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
        users = ["alice", "bob", "carol", self.focus_user]
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
        jobs.append(make(self.focus_user, 0))
        jobs.append(make(self.focus_user, 2))
        # CPU-only jobs for the watched user (these populate the CPU card). They
        # always report something, since only API users appear there.
        for _ in range(2):
            jobs.append(make(self.focus_user, None, kind=rng.choice(["determinate", "warmup"])))
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
    def collect_GPU(self) -> Tuple[List[GPUInfo], List[ProcInfo]]:
        self._ensure_jobs()
        assert self._jobs is not None
        rng = random.Random()  # only util/temperature jitter is random per tick
        gpus: List[GPUInfo] = []
        for gpu_id in range(4):
            total = self.GPU_TOTAL_GB[gpu_id] * 1024 ** 3
            gpus.append(GPUInfo(
                id=gpu_id, name=self.GPU_NAMES[gpu_id], mem_used=0, mem_total=total, mem_free=total, mem_util=rng.randint(0, 100), temperature=rng.randint(35, 85),
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
        gpu_procs = [p for g in gpus for p in g.procs]
        self._assign_numbers(gpu_procs)
        self._annotate_eta(gpu_procs)

        return gpus, gpu_procs

    def collect_CPU(self, gpu_pids: set) -> CPUInfo:
        """The watched user's CPU-only (never-on-GPU) reporting jobs."""
        self._ensure_jobs()
        assert self._jobs is not None
        user = self.focus_user or "frank"
        cpu_procs = [
            self._job_proc(job) for job in self._jobs
            if job["gpu"] is None and job["user"] == user
        ]
        cpu = CPUInfo(name="CPU")
        cpu.procs = cpu_procs
        self._assign_numbers(cpu_procs)
        self._annotate_eta(cpu_procs)

        return cpu

    def gpu_specs(self) -> List[Tuple[int, str, int]]:
        return [(i, name, self.GPU_TOTAL_GB[i] * 1024 ** 3) for i, name in enumerate(self.GPU_NAMES)]

    def collect_tmux(self) -> List[TmuxSession]:
        """Fabricate a couple of tmux sessions so ``--demo`` shows tmux mode."""
        self._ensure_jobs()
        assert self._jobs is not None
        # Reuse the synthetic jobs as the "commands" running inside panes.
        jobs = list(self._jobs)
        sessions: List[TmuxSession] = []
        plan = [
            ("main", True, [("0", "train"), ("1", "shell")]),
            ("exp1", False, [("0", "eval")]),
        ]
        pane_pid = 4000
        ji = 0
        for sname, attached, windows in plan:
            session = TmuxSession(name=sname, attached=attached)
            for w_i, (widx, wname) in enumerate(windows):
                shell = ProcInfo(
                    pid=pane_pid, pname="zsh", user=self.focus_user, mem=0,
                    runtime="1h 02m", cmd="-zsh", runtime_sec=3720,
                    sid=pane_pid, sname=f"tmux:{sname}",
                    s_start="-", s_start_ts=time.time() - 3720, rss=8 * 1024 ** 2,
                )
                procs = [shell]
                # Put one synthetic job as the command running in this pane.
                if jobs:
                    job = jobs[ji % len(jobs)]
                    ji += 1
                    proc = self._job_proc(job)
                    proc.sname = f"tmux:{sname}"
                    procs.append(proc)
                session.panes.append(TmuxPane(
                    session=sname, attached=attached, window_idx=int(widx),
                    window_name=wname, pane_idx=0, pane_pid=pane_pid, procs=procs,
                ))
                pane_pid += 1
            sessions.append(session)
        all_procs = [p for s in sessions for p in s.all_procs]
        self._assign_numbers(all_procs)
        self._annotate_eta(all_procs)
        return sessions


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
