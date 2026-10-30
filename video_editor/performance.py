"""Measured ASR calibration; short benchmarks are never extrapolated to long videos."""

import subprocess
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from video_editor import storage, transcription
from video_editor.cuts import tokens
from video_editor.models import PrepareConfig


def process_memory_mib():
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (n, ctypes.c_size_t)
                for n in (
                    "peak",
                    "working",
                    "paged_peak",
                    "paged",
                    "nonpaged_peak",
                    "nonpaged",
                    "pagefile",
                    "pagefile_peak",
                )
            ]

        value = Counters()
        value.cb = ctypes.sizeof(value)
        handle = ctypes.windll.kernel32.GetCurrentProcess
        handle.restype = wintypes.HANDLE
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if read(handle(), ctypes.byref(value), value.cb):
            return round(value.working / 1024 / 1024, 2)
    except (AttributeError, OSError):
        pass
    return None


def gpu():
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        name, total, used, load = result.stdout.strip().splitlines()[0].rsplit(",", 3)
        return dict(
            name=name.strip(),
            total_mib=int(total),
            used_mib=int(used),
            load_percent=int(load),
        )
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


class Monitor:
    def __enter__(self):
        self.samples = []
        self.ram = []
        self.stop = threading.Event()

        def sample():
            while not self.stop.is_set():
                value = gpu()
                if value:
                    self.samples.append(value)
                ram = process_memory_mib()
                if ram is not None:
                    self.ram.append(ram)
                self.stop.wait(0.75)

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        self.started = time.monotonic()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=6)
        self.result = dict(
            seconds=time.monotonic() - self.started,
            gpu_peak_mib=max((s["used_mib"] for s in self.samples), default=None),
            process_ram_peak_mib=max(self.ram, default=None),
            gpu_samples=len(self.samples),
        )


def quality(reference, candidate):
    old = tokens(" ".join(s["text"] for s in reference["segments"]))
    new = tokens(" ".join(s["text"] for s in candidate["segments"]))
    similarity = SequenceMatcher(None, old, new, autojunk=False).ratio()
    # Numerical/negative changes cannot be hidden by a good overall similarity score.
    anchors = lambda values: [
        t
        for t in values
        if any(c.isdigit() for c in t) or t in ("nao", "nunca", "jamais", "sem")
    ]
    return dict(
        token_similarity=similarity,
        anchors_equal=anchors(old) == anchors(new),
        eligible=bool(old) and similarity >= 0.98 and anchors(old) == anchors(new),
    )


def calibrate(wav, source, output):
    runs = []
    reference = None
    initial = gpu()
    if not initial:
        raise ValueError("Calibração CUDA requer NVIDIA disponível.")
    limit = initial["total_mib"] - 1024
    for batch in (1, 2, 4, 8):
        config = PrepareConfig(batch_size=batch).model_dump()
        try:
            # Warm loading separately so inference throughput is comparable across batches.
            with transcription._model_lock:
                transcription._model(dict(config))
            with Monitor() as monitor:
                transcript, actual = transcription.transcribe(wav, config, source)
            if reference is None:
                reference = transcript
            check = quality(reference, transcript)
            peak = monitor.result["gpu_peak_mib"]
            eligible = (
                check["eligible"]
                and actual["device"] == "cuda"
                and actual.get("batch_size", 1) == batch
                and peak is not None
                and peak <= limit
                and (
                    monitor.result["process_ram_peak_mib"] is None
                    or monitor.result["process_ram_peak_mib"] <= 12000
                )
            )
            runs.append(
                dict(
                    batch_size=batch,
                    seconds=actual["transcribe_seconds"],
                    measurement=monitor.result,
                    quality=check,
                    eligible=eligible,
                )
            )
        except Exception as error:
            runs.append(
                dict(batch_size=batch, eligible=False, error=type(error).__name__)
            )
    eligible = [r for r in runs if r["eligible"]]
    selected = (
        min(eligible, key=lambda r: r["seconds"])["batch_size"] if eligible else 1
    )
    result = dict(
        schema=1,
        model="small",
        compute_type="int8_float16",
        gpu=initial["name"],
        selected_batch_size=selected,
        runs=runs,
        created_at=storage.now(),
        sample_source=source,
        qualification="sample_only_not_long_video_or_human_quality",
    )
    storage.write(Path(output), result)
    return result


def selected_batch(path):
    path = Path(path)
    if not path.exists():
        return 1
    try:
        value = storage.read(path)
    except (ValueError, OSError):
        return 1
    device = gpu()
    if (
        not device
        or value.get("gpu") != device["name"]
        or value.get("model") != "small"
        or value.get("selected_batch_size") not in (1, 2, 4, 8)
    ):
        return 1
    return value["selected_batch_size"]
