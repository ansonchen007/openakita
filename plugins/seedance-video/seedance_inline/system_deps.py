"""Self-contained system dependency detector + fire-and-poll installer.

After SDK 0.7.0 retired ``openakita_plugin_sdk.contrib.DependencyGate`` and
the host-mounted ``/api/plugins/_sdk/deps/*`` SSE endpoint, plugins that
need a system binary (here: FFmpeg) must own their installer end-to-end.

Design rules
------------
- **White-listed argv only.** Install commands are hard-coded per platform;
  no ``shell=True``, no user-supplied tokens get spliced into argv.
- **Single dep installs serialise** behind one ``asyncio.Lock`` so a runaway
  client cannot fork N parallel ``winget`` invocations.
- **Fire-and-poll**, not SSE. ``start_install`` returns immediately; the
  frontend polls ``status`` every few seconds. The last 50 lines of stdout
  + stderr are kept in a ``deque`` so the UI can show an "Install log"
  panel without us having to keep an open connection.
- **Never raises** in the public API except for invalid arguments — every
  failure is folded into the returned dict's ``error`` field so callers do
  not need defensive try/except blocks.
- **Stdlib only**: ``asyncio``, ``shutil``, ``subprocess``, ``collections``,
  ``platform``, ``os``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

Platform = Literal["windows", "macos", "linux"]


def _current_platform() -> Platform:
    name = platform.system().lower()
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "macos"
    return "linux"


def _is_root() -> bool:
    """POSIX-only effective root check; Windows always returns False."""
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return False


@dataclass(frozen=True)
class InstallMethod:
    """One platform-specific install recipe."""

    platform: Platform
    strategy: str          # "winget" | "brew" | "apt" | "dnf" | "manual"
    command: tuple[str, ...] | None
    description: str
    requires_sudo: bool = False
    estimated_seconds: int = 120
    manual_url: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        """Public-safe view (argv hidden — only the strategy + command_hint)."""
        return {
            "platform": self.platform,
            "strategy": self.strategy,
            "description": self.description,
            "requires_sudo": self.requires_sudo,
            "estimated_seconds": self.estimated_seconds,
            "manual_url": self.manual_url,
            "command_hint": " ".join(self.command) if self.command else "",
        }


@dataclass(frozen=True)
class DepSpec:
    """Declarative spec for one system dependency."""

    id: str
    display_name: str
    description: str
    homepage: str
    probes: tuple[str, ...]
    version_argv: tuple[str, ...]
    version_regex: str
    install_methods: tuple[InstallMethod, ...]


# ── Catalog (keep tiny; only what seedance-video itself needs) ────────────

_FFMPEG = DepSpec(
    id="ffmpeg",
    display_name="FFmpeg",
    description=(
        "Video / audio toolkit used for long-video stitching in the "
        "Storyboard tab and for video thumbnail previews in the Asset library."
    ),
    homepage="https://ffmpeg.org/download.html",
    probes=("ffmpeg",),
    version_argv=("ffmpeg", "-version"),
    version_regex=r"ffmpeg version\s+(\S+)",
    install_methods=(
        InstallMethod(
            platform="windows",
            strategy="winget",
            command=(
                "winget", "install", "--id", "Gyan.FFmpeg",
                "-e", "--accept-source-agreements", "--accept-package-agreements",
            ),
            description="Install FFmpeg via Windows Package Manager (winget).",
            requires_sudo=False,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="macos",
            strategy="brew",
            command=("brew", "install", "ffmpeg"),
            description="Install FFmpeg via Homebrew.",
            requires_sudo=False,
            estimated_seconds=180,
        ),
        InstallMethod(
            platform="linux",
            strategy="apt",
            command=("apt-get", "install", "-y", "ffmpeg"),
            description="Install FFmpeg via apt (Debian / Ubuntu). Requires root.",
            requires_sudo=True,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="linux",
            strategy="dnf",
            command=("dnf", "install", "-y", "ffmpeg"),
            description="Install FFmpeg via dnf (Fedora / RHEL). Requires root.",
            requires_sudo=True,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="linux",
            strategy="manual",
            command=None,
            description="No package manager available — download a static build.",
            manual_url="https://johnvansickle.com/ffmpeg/",
        ),
    ),
)


_SPECS: dict[str, DepSpec] = {_FFMPEG.id: _FFMPEG}


@dataclass
class _RunState:
    """Mutable per-dep runtime state (not exposed directly)."""

    found: bool = False
    version: str = ""
    location: str = ""
    detect_error: str = ""

    busy: bool = False
    started_at: float = 0.0
    finished_at: float = 0.0
    return_code: int | None = None
    method_strategy: str = ""
    install_error: str = ""
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=50))
    _proc: asyncio.subprocess.Process | None = None
    _drain_task: asyncio.Task[Any] | None = None


class SystemDepsManager:
    """Detect + install whitelisted system binaries with one method per dep.

    Methods exposed to plugin routes (all sync return dicts):

    - :meth:`list_components` — full snapshot for the Settings page
    - :meth:`detect` — re-probe one dep
    - :meth:`methods` — install methods that apply to the current OS
    - :meth:`start_install` — kick off install (idempotent if already busy)
    - :meth:`status` — live status snapshot during/after an install
    - :meth:`aclose` — cancel any in-flight drain task on plugin unload
    """

    def __init__(self) -> None:
        self._state: dict[str, _RunState] = {dep_id: _RunState() for dep_id in _SPECS}
        self._locks: dict[str, asyncio.Lock] = {dep_id: asyncio.Lock() for dep_id in _SPECS}
        self._platform: Platform = _current_platform()

    # ── Detection ──────────────────────────────────────────────────────

    def detect(self, dep_id: str, *, force: bool = True) -> dict[str, Any]:
        spec = self._require(dep_id)
        st = self._state[dep_id]
        if not force and st.location:
            return self._detect_dict(spec, st)

        st.detect_error = ""
        location = ""
        for probe in spec.probes:
            found_path = shutil.which(probe)
            if found_path:
                location = found_path
                break

        if not location:
            st.found = False
            st.location = ""
            st.version = ""
            return self._detect_dict(spec, st)

        st.found = True
        st.location = location
        st.version = self._safe_version(spec, location)
        return self._detect_dict(spec, st)

    @staticmethod
    def _detect_dict(spec: DepSpec, st: _RunState) -> dict[str, Any]:
        return {
            "id": spec.id,
            "display_name": spec.display_name,
            "homepage": spec.homepage,
            "found": st.found,
            "version": st.version,
            "location": st.location,
            "error": st.detect_error,
        }

    @staticmethod
    def _safe_version(spec: DepSpec, location: str) -> str:
        if not spec.version_argv:
            return ""
        argv = (location,) + tuple(spec.version_argv[1:])
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=5, check=False,
            )
        except Exception as exc:
            logger.debug("Version probe for %s failed: %s", spec.id, exc)
            return ""
        blob = (proc.stdout or "") + "\n" + (proc.stderr or "")
        match = re.search(spec.version_regex, blob)
        return match.group(1) if match else ""

    # ── Methods listing ────────────────────────────────────────────────

    def methods(self, dep_id: str) -> list[dict[str, Any]]:
        spec = self._require(dep_id)
        out: list[dict[str, Any]] = []
        for m in spec.install_methods:
            if m.platform != self._platform:
                continue
            # Hide manager-based methods whose binary is not even on PATH
            # (e.g. ``apt-get`` on a Fedora box). ``manual`` always shows.
            if m.command and m.strategy != "manual":
                if not shutil.which(m.command[0]):
                    continue
            out.append(m.to_public_dict())
        return out

    # ── Aggregate snapshot ─────────────────────────────────────────────

    def list_components(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for dep_id in _SPECS:
            self.detect(dep_id, force=True)
            spec = _SPECS[dep_id]
            st = self._state[dep_id]
            items.append({
                **self._detect_dict(spec, st),
                "description": spec.description,
                "platform": self._platform,
                "is_root": _is_root(),
                "methods": self.methods(dep_id),
                "busy": st.busy,
                "last_install": self._install_dict(st),
            })
        return items

    # ── Installation ───────────────────────────────────────────────────

    async def start_install(
        self,
        dep_id: str,
        *,
        method_index: int = 0,
    ) -> dict[str, Any]:
        spec = self._require(dep_id)
        st = self._state[dep_id]
        lock = self._locks[dep_id]

        if st.busy or lock.locked():
            return {
                "ok": True,
                "busy": True,
                "started_at": st.started_at,
                "method_strategy": st.method_strategy,
                "note": "install_already_running",
            }

        methods = self.methods(dep_id)
        if not methods:
            return {
                "ok": False,
                "busy": False,
                "error": f"no automated installer available on {self._platform}",
            }
        if method_index < 0 or method_index >= len(methods):
            return {
                "ok": False,
                "busy": False,
                "error": f"method_index {method_index} out of range (have {len(methods)})",
            }

        chosen_public = methods[method_index]
        # Resolve back to the underlying argv (we only exposed command_hint)
        method = self._resolve_method(spec, chosen_public)
        if method is None or method.command is None:
            return {
                "ok": False,
                "busy": False,
                "error": f"method '{chosen_public.get('strategy')}' has no executable command",
                "manual_url": chosen_public.get("manual_url", ""),
            }

        if method.requires_sudo and not _is_root():
            return {
                "ok": False,
                "busy": False,
                "error": "requires_sudo",
                "command_hint": " ".join(method.command),
            }

        st.busy = True
        st.started_at = time.time()
        st.finished_at = 0.0
        st.return_code = None
        st.install_error = ""
        st.method_strategy = method.strategy
        st.log_tail.clear()
        st.log_tail.append(f"$ {' '.join(method.command)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *method.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            st.busy = False
            st.finished_at = time.time()
            st.install_error = f"installer binary not found: {exc}"
            st.log_tail.append(st.install_error)
            return {
                "ok": False,
                "busy": False,
                "error": st.install_error,
            }
        except Exception as exc:
            st.busy = False
            st.finished_at = time.time()
            st.install_error = f"failed to spawn installer: {exc}"
            st.log_tail.append(st.install_error)
            return {
                "ok": False,
                "busy": False,
                "error": st.install_error,
            }

        st._proc = proc
        # Run the drain in a background task so this method returns ASAP.
        st._drain_task = asyncio.create_task(
            self._drain_and_finalize(dep_id, proc, lock),
            name=f"sysdeps:install:{dep_id}",
        )

        return {
            "ok": True,
            "busy": True,
            "started_at": st.started_at,
            "method_strategy": st.method_strategy,
            "estimated_seconds": method.estimated_seconds,
        }

    async def _drain_and_finalize(
        self,
        dep_id: str,
        proc: asyncio.subprocess.Process,
        lock: asyncio.Lock,
    ) -> None:
        st = self._state[dep_id]
        async with lock:
            try:
                async def pump(reader: asyncio.StreamReader | None, _label: str) -> None:
                    if reader is None:
                        return
                    while True:
                        raw = await reader.readline()
                        if not raw:
                            break
                        try:
                            text = raw.decode("utf-8", errors="replace").rstrip()
                        except Exception:
                            text = repr(raw)
                        if text:
                            st.log_tail.append(text)

                await asyncio.gather(
                    pump(proc.stdout, "stdout"),
                    pump(proc.stderr, "stderr"),
                )
                rc = await proc.wait()
                st.return_code = rc
                if rc != 0:
                    st.install_error = f"installer exited with code {rc}"
            except asyncio.CancelledError:
                # Plugin is unloading; try to terminate the child cleanly.
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                st.install_error = "install cancelled (plugin unloading)"
                raise
            except Exception as exc:
                logger.exception("install drain crashed for %s", dep_id)
                st.install_error = f"internal_error: {exc}"
            finally:
                st.finished_at = time.time()
                st.busy = False
                st._proc = None
                # Re-detect so subsequent /components snapshots reflect the
                # new on-disk state without needing another /detect call.
                try:
                    self.detect(dep_id, force=True)
                except Exception as exc:
                    logger.debug("post-install re-detect failed: %s", exc)

    # ── Status ─────────────────────────────────────────────────────────

    def status(self, dep_id: str) -> dict[str, Any]:
        spec = self._require(dep_id)
        st = self._state[dep_id]
        return {
            "ok": True,
            "id": spec.id,
            **self._install_dict(st),
            "found": st.found,
            "version": st.version,
            "location": st.location,
        }

    @staticmethod
    def _install_dict(st: _RunState) -> dict[str, Any]:
        elapsed = 0.0
        if st.started_at:
            end = st.finished_at if st.finished_at else time.time()
            elapsed = max(0.0, end - st.started_at)
        return {
            "busy": st.busy,
            "started_at": st.started_at,
            "finished_at": st.finished_at,
            "elapsed_sec": round(elapsed, 1),
            "return_code": st.return_code,
            "method_strategy": st.method_strategy,
            "error": st.install_error,
            "log_tail": list(st.log_tail),
        }

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Cancel any in-flight install drain (plugin unload)."""
        for st in self._state.values():
            task = st._drain_task
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ── Helpers ────────────────────────────────────────────────────────

    def _require(self, dep_id: str) -> DepSpec:
        spec = _SPECS.get(dep_id)
        if spec is None:
            raise ValueError(f"unknown dep_id: {dep_id!r}")
        return spec

    def _resolve_method(
        self, spec: DepSpec, public: dict[str, Any],
    ) -> InstallMethod | None:
        """Match a public method-dict back to the original ``InstallMethod``.

        We use ``(platform, strategy)`` as the natural key — combinations are
        unique within a single ``DepSpec``.
        """
        target_strategy = public.get("strategy")
        for m in spec.install_methods:
            if m.platform == self._platform and m.strategy == target_strategy:
                return m
        return None


__all__ = [
    "DepSpec",
    "InstallMethod",
    "SystemDepsManager",
]
