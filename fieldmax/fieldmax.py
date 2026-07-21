"""Python wrapper around Coherent's ``FieldMax2Lib.dll``.

The DLL exposes a small C API for talking to a FieldMaxII laser power meter
over USB. This module wraps the relevant entry points in a Pythonic class
with proper ``ctypes`` argument types, return-code checking and a
sample-buffer aware ``get_data`` call.

Notes on the device protocol
----------------------------
* The meter samples internally at 10 Hz.
* ``fm2LibGetData`` returns *up to 8* (power, period) float pairs per call,
  oldest-first, from the meter's internal queue. 8 is the **read chunk
  size**, not the queue depth -- the queue itself appears to grow
  unboundedly when not drained, so polling slower than 8 samples / 0.8 s
  causes the queue to back up and reads then return ancient samples.
  The DLL writes the actual count of valid samples back into the in/out
  length parameter. The newest sample is the **last** one in the buffer.
* ``fm2LibSync`` *clears* the ring buffer and re-arms the measurement.
  Calling it on every read therefore forces a wait of up to ~100 ms for the
  next sample to arrive and is almost never what you want -- call it once
  after opening the driver, then poll ``fm2LibGetData`` faster than 10 Hz
  (or use :meth:`FieldMax.get_latest_power`, which drains the queue).
"""

from __future__ import annotations

import ctypes as c
import logging
import os
import time
from collections.abc import Sequence
from typing import Final

log = logging.getLogger(__name__)

DEFAULT_DLL_PATH: Final[str] = (
    r"C:\Program Files (x86)\Coherent\FieldMaxII PC\Drivers\Win10"
    r"\FieldMax2Lib\x64\FieldMax2Lib.dll"
)

# Size of the ring buffer returned by fm2LibGetData (8 samples * 8 bytes each).
_DATA_BUFFER_SAMPLES: Final[int] = 8
_DATA_BUFFER_BYTES: Final[int] = _DATA_BUFFER_SAMPLES * 8

# Maximum time we are willing to wait for ``fm2LibGetZeroReply`` to stop
# returning "still busy" (1) before raising. The zeroing operation usually
# completes in well under a second.
_ZEROING_TIMEOUT_S: Final[float] = 10.0
_ZEROING_POLL_INTERVAL_S: Final[float] = 0.05


class FieldMaxError(RuntimeError):
    """Raised when an ``fm2Lib*`` call returns a non-zero status code."""

    def __init__(self, function: str, code: int) -> None:
        super().__init__(f"{function} failed with code {code}")
        self.function = function
        self.code = code


class FieldMaxTimeoutError(FieldMaxError):
    """Raised when zeroing does not complete within the allotted time."""


def _check(function: str, code: int) -> int:
    """Raise :class:`FieldMaxError` if ``code`` is non-zero, else return it."""
    if code != 0:
        raise FieldMaxError(function, code)
    return code


def _warn(function: str, code: int) -> int:
    """Log a warning if ``code`` is non-zero. Return ``code`` either way.

    Used for DLL calls whose non-zero return values are not necessarily
    fatal. Empirically, ``FieldMax2Lib.dll`` returns -1 from many of its
    entry points even when the call succeeds and the output buffers are
    valid; the original vendor wrapper ignored these silently. We surface
    them as warnings so genuine problems are visible without breaking
    otherwise working sessions.
    """
    if code != 0:
        log.debug("%s returned non-zero status %d", function, code)
    return code


class FieldMax:
    """High-level wrapper around ``FieldMax2Lib.dll``.

    The DLL is process-global; only one :class:`FieldMax` instance can talk
    to a given meter at a time. The wrapper supports use as a context
    manager, in which case :meth:`open_driver` and :meth:`sync` are called
    on entry and :meth:`close_driver` / :meth:`deinit` on exit.

    Example
    -------
    >>> with FieldMax() as fm:
    ...     print(fm.get_serial_number())
    ...     while running:
    ...         power = fm.get_latest_power()
    ...         if power is not None:
    ...             ...

    """

    def __init__(self, dll_path: str | None = None) -> None:
        path = dll_path or os.environ.get("FIELDMAX2LIB_DLL") or DEFAULT_DLL_PATH
        if not os.path.exists(path):
            raise FileNotFoundError(f"FieldMax2Lib.dll not found at {path!r}")
        self._dll_path = path
        self.o = c.windll.LoadLibrary(path)
        self._configure_prototypes()
        # Pre-allocate the data buffer once; we rewrite it in place on every
        # ``get_data`` call to avoid GC churn during fast monitor loops.
        self._data_buf = (c.c_uint8 * _DATA_BUFFER_BYTES)()
        self._data_len = c.c_int16(_DATA_BUFFER_SAMPLES)

    # ------------------------------------------------------------------ #
    # ctypes prototype setup
    # ------------------------------------------------------------------ #
    def _configure_prototypes(self) -> None:
        """Set ``restype`` / ``argtypes`` for every DLL entry point we use."""
        self._fm2LibOpenDriver = self.o.fm2LibOpenDriver
        self._fm2LibOpenDriver.restype = c.c_int32
        self._fm2LibOpenDriver.argtypes = [c.c_int16]

        self._fm2LibCloseDriver = self.o.fm2LibCloseDriver
        self._fm2LibCloseDriver.restype = c.c_int16
        self._fm2LibCloseDriver.argtypes = [c.c_int32]

        self._fm2LibDeInit = self.o.fm2LibDeInit
        self._fm2LibDeInit.restype = c.c_int16
        self._fm2LibDeInit.argtypes = []

        self._fm2LibSync = self.o.fm2LibSync
        self._fm2LibSync.restype = c.c_int16
        self._fm2LibSync.argtypes = [c.c_int32]

        self._fm2LibGetSerialNumber = self.o.fm2LibGetSerialNumber
        self._fm2LibGetSerialNumber.restype = c.c_int16
        self._fm2LibGetSerialNumber.argtypes = [
            c.c_int32,
            c.POINTER(c.c_char * 16),
            c.POINTER(c.c_int16),
        ]

        self._fm2LibGetData = self.o.fm2LibGetData
        self._fm2LibGetData.restype = c.c_int16
        self._fm2LibGetData.argtypes = [
            c.c_int32,
            c.POINTER(c.c_uint8 * _DATA_BUFFER_BYTES),
            c.POINTER(c.c_int16),
        ]

        self._fm2LibPackagedSendReply = self.o.fm2LibPackagedSendReply
        self._fm2LibPackagedSendReply.restype = c.c_int16
        self._fm2LibPackagedSendReply.argtypes = [
            c.c_int32,
            c.c_char_p,
            c.POINTER(c.c_char * 100),
            c.POINTER(c.c_int16),
        ]

        self._fm2LibGetStatusChangeList = self.o.fm2LibGetStatusChangeList
        self._fm2LibGetStatusChangeList.restype = c.c_int16
        self._fm2LibGetStatusChangeList.argtypes = [c.POINTER(c.c_uint16 * 3)]

        self._fm2LibZeroStart = self.o.fm2LibZeroStart
        self._fm2LibZeroStart.restype = c.c_int16
        self._fm2LibZeroStart.argtypes = [c.c_int32]

        self._fm2LibGetZeroReply = self.o.fm2LibGetZeroReply
        self._fm2LibGetZeroReply.restype = c.c_int16
        self._fm2LibGetZeroReply.argtypes = [c.c_int32]

    # ------------------------------------------------------------------ #
    # Driver lifecycle
    # ------------------------------------------------------------------ #
    def open_driver(self) -> None:
        """Open the connection to the meter. Call once before any read."""
        _check("fm2LibOpenDriver", self._fm2LibOpenDriver(c.c_int16(0)))

    def close_driver(self) -> None:
        """Close the connection to the meter."""
        _warn("fm2LibCloseDriver", self._fm2LibCloseDriver(c.c_int32(0)))

    def deinit(self) -> None:
        """Tear down the DLL. Call after :meth:`close_driver`."""
        _warn("fm2LibDeInit", self._fm2LibDeInit())

    def sync(self) -> None:
        """Clear the meter's sample buffer and re-arm the measurement.

        Call once after :meth:`open_driver`. Calling it on every read
        defeats the buffering and forces a ~100 ms wait for the next
        sample at the device's 10 Hz update rate.
        """
        _warn("fm2LibSync", self._fm2LibSync(0))

    # ------------------------------------------------------------------ #
    # Data acquisition
    # ------------------------------------------------------------------ #
    def get_data(self) -> tuple[list[float], list[float]]:
        """Read all buffered (power, period) samples since the previous call.

        Returns
        -------
        tuple[list[float], list[float]]
            ``(powers_W, periods_s)``. Both lists have the same length,
            between 0 and 8. The most recent sample is the last element.
            An empty result means no new samples have arrived since the
            last call (the meter updates at 10 Hz).
        """
        self._data_len.value = _DATA_BUFFER_SAMPLES
        _warn(
            "fm2LibGetData",
            self._fm2LibGetData(
                0, c.pointer(self._data_buf), c.pointer(self._data_len)
            ),
        )
        n = self._data_len.value
        if n <= 0:
            return [], []
        fp = c.cast(self._data_buf, c.POINTER(c.c_float))
        powers = [float(fp[2 * i]) for i in range(n)]
        periods = [float(fp[2 * i + 1]) for i in range(n)]
        return powers, periods

    def get_powers(self) -> list[float]:
        """Read all buffered power samples (W) since the previous call."""
        return self.get_data()[0]

    def get_latest_power(self) -> float | None:
        """Return the newest buffered power sample in W, or ``None`` if empty.

        Drains the meter's internal sample queue and returns only the
        most recent sample. This is required because ``fm2LibGetData``
        only returns up to 8 samples per call (oldest-first); when the
        caller polls slower than ~12 Hz the queue backs up and a single
        ``get_powers()`` call would otherwise return samples that are
        seconds old.

        ``None`` means the meter has not produced a new sample since the
        last drain. Callers polling faster than 10 Hz should typically
        carry forward the previous reading in that case.
        """
        latest: float | None = None
        while True:
            powers = self.get_powers()
            if not powers:
                # Queue is empty. If we already grabbed a sample on a
                # previous iteration, return it; otherwise None.
                return latest
            latest = powers[-1]
            if len(powers) < _DATA_BUFFER_SAMPLES:
                # Got fewer than a full chunk -> queue exhausted.
                return latest

    # ------------------------------------------------------------------ #
    # Misc queries
    # ------------------------------------------------------------------ #
    def get_serial_number(self) -> str:
        """Return the meter's serial number as a string."""
        buf = (c.c_char * 16)()
        size = c.c_int16(16)
        _warn(
            "fm2LibGetSerialNumber",
            self._fm2LibGetSerialNumber(c.c_int32(0), buf, c.pointer(size)),
        )
        return buf.value.decode()

    def send_reply(self, command: str | bytes) -> str:
        """Send a raw "Meter Settings Command" string and return the reply."""
        if isinstance(command, str):
            command = command.encode("ascii")
        reply = (c.c_char * 100)()
        size = c.c_int16(100)
        _warn(
            "fm2LibPackagedSendReply",
            self._fm2LibPackagedSendReply(
                c.c_int32(0), c.c_char_p(command), c.pointer(reply), c.pointer(size)
            ),
        )
        return reply.value.decode()

    def get_status_change_list(self) -> list[int]:
        """Return the three status-change flags from the DLL."""
        buf = (c.c_uint16 * 3)()
        _warn(
            "fm2LibGetStatusChangeList",
            self._fm2LibGetStatusChangeList(c.pointer(buf)),
        )
        return list(buf)

    # ------------------------------------------------------------------ #
    # Zeroing
    # ------------------------------------------------------------------ #
    def zeroing(self, timeout: float = _ZEROING_TIMEOUT_S) -> None:
        """Run the meter's zeroing routine, blocking until it completes.

        Parameters
        ----------
        timeout : float
            Maximum number of seconds to wait for the routine to finish.
            Raises :class:`FieldMaxTimeoutError` on timeout.
        """
        _warn("fm2LibZeroStart", self._fm2LibZeroStart(c.c_int32(0)))
        deadline = time.monotonic() + timeout
        while True:
            ans = self._fm2LibGetZeroReply(c.c_int32(0))
            if ans != 1:  # 0 = done, anything other than 1 also exits the loop
                _warn("fm2LibGetZeroReply", ans)
                return
            if time.monotonic() >= deadline:
                raise FieldMaxTimeoutError("fm2LibGetZeroReply", ans)
            time.sleep(_ZEROING_POLL_INTERVAL_S)

    # ------------------------------------------------------------------ #
    # Context manager
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "FieldMax":
        self.open_driver()
        self.sync()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.close_driver()
        except FieldMaxError as e:
            log.warning("close_driver failed: %s", e)
        try:
            self.deinit()
        except FieldMaxError as e:
            log.warning("deinit failed: %s", e)

    # ------------------------------------------------------------------ #
    # Backwards-compatible aliases (deprecated)
    # ------------------------------------------------------------------ #
    # The original API used inconsistent camelCase names and a buggy
    # ``get_dataPoint`` that returned the *oldest* buffered sample after a
    # forced ``sync()``. The aliases below preserve callable names for
    # existing code; new code should use the snake_case methods above.

    def openDriver(self) -> None:  # noqa: N802 - legacy name
        self.open_driver()

    def closeDriver(self) -> None:  # noqa: N802 - legacy name
        self.close_driver()

    def deInit(self) -> None:  # noqa: N802 - legacy name
        self.deinit()

    def get_SerialNumber(self) -> str:  # noqa: N802 - legacy name
        return self.get_serial_number()

    def get_dataArray(self) -> Sequence[float]:  # noqa: N802 - legacy name
        """Deprecated: use :meth:`get_powers`. Returns powers since last call."""
        return self.get_powers()

    def get_dataPoint(self) -> float:  # noqa: N802 - legacy name
        """Deprecated: use :meth:`get_latest_power`.

        Returns the most recent buffered sample, or 0.0 if the buffer was
        empty. Unlike the original, this does **not** call ``sync()``.
        """
        latest = self.get_latest_power()
        return 0.0 if latest is None else latest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with FieldMax() as fm:
        print("Serial:", fm.get_serial_number())
        # Give the meter ~120 ms to fill the buffer with at least one sample.
        time.sleep(0.12)
        print("Latest power [W]:", fm.get_latest_power())
        fm.zeroing()
        time.sleep(0.12)
        print("Latest power after zeroing [W]:", fm.get_latest_power())
