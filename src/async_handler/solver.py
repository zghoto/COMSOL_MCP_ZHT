"""Asynchronous solver handler for COMSOL simulations."""

import threading
import logging
from typing import Optional, Callable
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile

from ..utils.studies import solve_studies

logger = logging.getLogger(__name__)


@contextmanager
def capture_solver_log(model_util):
    """Scope the COMSOL client-side progress log to a single run."""
    if model_util is None:
        yield None
        return
    with NamedTemporaryFile(prefix="comsol-solve-", suffix=".log", delete=False) as stream:
        path = stream.name
    try:
        model_util.showProgress(path)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    try:
        yield path
    finally:
        try:
            model_util.showProgress(False)
        except Exception:
            logger.warning("Could not disable COMSOL progress logging.", exc_info=True)


def read_solver_log(path):
    """Read a bounded tail of a log created by capture_solver_log."""
    try:
        with Path(path).open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 8192))
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


class SolverStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SolverProgress:
    """Progress information for a solving operation."""
    status: SolverStatus = SolverStatus.IDLE
    progress: float = 0.0
    message: str = ""
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    error: Optional[str] = None
    study_name: Optional[str] = None
    model_name: Optional[str] = None
    log_file: Optional[str] = None
    runs: Optional[list[dict]] = None
    
    def to_dict(self) -> dict:
        report = {
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "error": self.error,
            "study_name": self.study_name,
            "model_name": self.model_name,
            "elapsed_seconds": (
                (self.end_time or datetime.now()) - self.start_time
            ).total_seconds() if self.start_time else 0,
        }
        if self.log_file:
            report["log_file"] = self.log_file
            report["log_tail"] = read_solver_log(self.log_file)
        if self.runs is not None:
            report["runs"] = self.runs
        return report


class AsyncSolver:
    """Manages asynchronous solving operations for COMSOL models."""
    
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._progress: SolverProgress = SolverProgress()
        self._cancel_flag: bool = False
        self._lock: threading.Lock = threading.Lock()
    
    @property
    def progress(self) -> SolverProgress:
        with self._lock:
            return self._progress
    
    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._progress.status == SolverStatus.RUNNING
    
    def start_solve(
        self,
        model,
        study_name: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        model_util=None,
    ) -> bool:
        """
        Start solving a study in a background thread.
        
        Args:
            model: The COMSOL model to solve
            study_name: Name of the study to solve (None for all studies)
            progress_callback: Optional callback for progress updates
        
        Returns:
            True if solving started, False if already running
        """
        with self._lock:
            if self._progress.status == SolverStatus.RUNNING:
                return False
            
            self._cancel_flag = False
            self._progress = SolverProgress(
                status=SolverStatus.RUNNING,
                progress=0.0,
                message="Starting solver...",
                start_time=datetime.now(),
                study_name=study_name,
                model_name=model.name() if hasattr(model, 'name') else None,
            )
        
        def solve_thread():
            try:
                with capture_solver_log(model_util) as path:
                    with self._lock:
                        self._progress.log_file = path
                        self._progress.message = f"Solving study: {study_name or 'all'}..."
                    runs = solve_studies(model, study_name, cancelled=lambda: self._cancel_flag)
                with self._lock:
                    self._progress.runs = runs
                if self._cancel_flag:
                    self._set_cancelled()
                    return

                if not runs or not runs[-1]["success"]:
                    raise RuntimeError(runs[-1]["error"] if runs else "No study was run.")

                with self._lock:
                    self._progress.status = SolverStatus.COMPLETED
                    self._progress.progress = 1.0
                    self._progress.message = "Solving completed successfully."
                    self._progress.end_time = datetime.now()
                
                if progress_callback:
                    progress_callback(1.0, "Completed")
                    
            except Exception as e:
                error_msg = str(e)
                
                with self._lock:
                    self._progress.status = SolverStatus.FAILED
                    self._progress.error = error_msg
                    self._progress.message = f"Solving failed: {error_msg}"
                    self._progress.end_time = datetime.now()
                
                if progress_callback:
                    progress_callback(-1.0, f"Error: {error_msg}")
        
        self._thread = threading.Thread(target=solve_thread, daemon=True)
        self._thread.start()
        return True
    
    def _set_cancelled(self):
        """Set status to cancelled."""
        with self._lock:
            self._progress.status = SolverStatus.CANCELLED
            self._progress.message = "Solving was cancelled by user."
            self._progress.end_time = datetime.now()
    
    def cancel(self) -> bool:
        """
        Request cancellation of the current solving operation.
        
        Note: This sets a flag but the actual COMSOL solver may continue
        until the next checkpoint. Full cancellation support depends on
        the COMSOL version and solver type.
        
        Returns:
            True if cancellation was requested, False if not running
        """
        with self._lock:
            if self._progress.status != SolverStatus.RUNNING:
                return False
            self._cancel_flag = True
            self._progress.message = "Cancellation requested..."
            return True
    
    def wait(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for the solving operation to complete.
        
        Args:
            timeout: Maximum time to wait in seconds (None for indefinite)
        
        Returns:
            True if solving completed, False if timeout reached
        """
        if self._thread is None:
            return True
        
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()
    
    def get_progress(self) -> dict:
        """Get current solving progress as a dictionary."""
        return self.progress.to_dict()
    
    def reset(self):
        """Reset the solver state."""
        with self._lock:
            self._progress = SolverProgress()
            self._cancel_flag = False


async_solver = AsyncSolver()
