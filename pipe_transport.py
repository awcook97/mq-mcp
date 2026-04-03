"""Platform-aware pipe transport for mq_actor.

Windows: win32 overlapped named-pipe I/O (existing behaviour).
Linux:   Unix domain socket (AF_UNIX SOCK_STREAM) with framed reads.

On Linux set pipe_name in config.json to a socket path, e.g. /tmp/mqpipe.
"""

import platform
import socket
import struct

_IS_WINDOWS = platform.system() == "Windows"
_HEADER_SIZE = 16  # must match mq_actor._HEADER_FMT packed size

# ---------------------------------------------------------------------------
# Windows backend
# ---------------------------------------------------------------------------

if _IS_WINDOWS:
    import pywintypes
    import win32api
    import win32event
    import win32file
    import win32pipe

    def _win32_connect_pipe(pipe_name: str):
        handle = win32file.CreateFile(
            pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            win32file.FILE_FLAG_OVERLAPPED,
            None,
        )
        win32pipe.SetNamedPipeHandleState(  
            handle, # type: ignore[arg-type]
            win32pipe.PIPE_READMODE_MESSAGE,
            None,
            None)
        return handle

    def _win32_close_pipe(handle):
        try:
            win32file.CloseHandle(handle)
        except Exception:
            pass

    def _win32_pipe_read(handle, buf_size: int, timeout_ms: int):
        """Blocking overlapped read.  Returns bytes or None on timeout."""
        buf = win32file.AllocateReadBuffer(buf_size)
        ov  = pywintypes.OVERLAPPED()
        ov.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            try:
                win32file.ReadFile(handle, buf, ov)
            except pywintypes.error as e:
                if e.winerror != 997:   # ERROR_IO_PENDING is expected
                    raise
            rc = win32event.WaitForSingleObject(ov.hEvent, timeout_ms)
            if rc == win32event.WAIT_TIMEOUT:
                win32file.CancelIo(handle)
                win32event.WaitForSingleObject(ov.hEvent, win32event.INFINITE)
                return None
            n = win32file.GetOverlappedResult(handle, ov, False)
            return bytes(buf[:n]) # type: ignore[arg-type]
        finally:
            win32api.CloseHandle(ov.hEvent)

    def _win32_pipe_write(handle, data: bytes, timeout_ms: int = 5_000):
        """Blocking overlapped write."""
        ov = pywintypes.OVERLAPPED()
        ov.hEvent = win32event.CreateEvent(None, True, False, None)
        try:
            try:
                win32file.WriteFile(handle, data, ov)
            except pywintypes.error as e:
                if e.winerror != 997:
                    raise
            rc = win32event.WaitForSingleObject(ov.hEvent, timeout_ms)
            if rc == win32event.WAIT_TIMEOUT:
                win32file.CancelIo(handle)
                raise TimeoutError("WriteFile timed out")
            win32file.GetOverlappedResult(handle, ov, False)
        finally:
            win32api.CloseHandle(ov.hEvent)

    _PIPE_ERROR = pywintypes.error
    connect_pipe = _win32_connect_pipe
    close_pipe   = _win32_close_pipe
    pipe_read    = _win32_pipe_read
    pipe_write   = _win32_pipe_write
    PIPE_ERROR   = _PIPE_ERROR

# ---------------------------------------------------------------------------
# Linux backend — Unix domain socket
# ---------------------------------------------------------------------------

else:
    # Linux: connect to the TCP bridge (mqpipe_bridge.exe running under Wine).
    # pipe_name should be "host:port", e.g. "127.0.0.1:29999"
    _BRIDGE_DEFAULT_HOST = "127.0.0.1"
    _BRIDGE_DEFAULT_PORT = 29999

    def _parse_addr(pipe_name: str):
        if ":" in pipe_name and not pipe_name.startswith("\\"):
            host, port = pipe_name.rsplit(":", 1)
            return host, int(port)
        return _BRIDGE_DEFAULT_HOST, _BRIDGE_DEFAULT_PORT

    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("bridge socket closed")
            buf += chunk
        return buf

    def _posix_connect_pipe(pipe_name: str) -> socket.socket:
        host, port = _parse_addr(pipe_name)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
        sock.setblocking(True)
        return sock

    def _posix_close_pipe(handle: socket.socket) -> None:
        try:
            handle.close()
        except Exception:
            pass

    def _posix_pipe_read(handle: socket.socket, buf_size: int, timeout_ms: int) -> bytes | None:
        """Framed read of one complete message. Returns bytes or None on timeout."""
        handle.settimeout(timeout_ms / 1000.0)
        try:
            header = _recv_exact(handle, _HEADER_SIZE)
        except (TimeoutError, socket.timeout):
            return None
        # length is the last uint32 in the header (little-endian, offset 12)
        (length,) = struct.unpack_from("<I", header, 12)
        handle.settimeout(None)
        payload = _recv_exact(handle, length) if length else b""
        return header + payload

    def _posix_pipe_write(handle: socket.socket, data: bytes, timeout_ms: int = 5_000) -> None:
        handle.settimeout(timeout_ms / 1000.0)
        try:
            handle.sendall(data)
        except (TimeoutError, socket.timeout):
            raise TimeoutError("bridge socket write timed out")
        finally:
            handle.settimeout(None)

    connect_pipe = _posix_connect_pipe
    close_pipe   = _posix_close_pipe
    pipe_read    = _posix_pipe_read
    pipe_write   = _posix_pipe_write
    PIPE_ERROR   = OSError
