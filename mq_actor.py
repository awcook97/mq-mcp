"""Python Actor client for MacroQuest's named pipe protocol.

Connects to \\\\.\\pipe\\mqpipe, registers as a named Actor, and makes
RPC calls (CallAndResponse) to Lua Actor mailboxes running inside MQ.

Wire format per message:
    [16-byte MQMessageHeader][protobuf payload]

MQMessageHeader layout (packed, little-endian):
    uint8   protoVersion  (0 = V0)
    uint8   mode          (0=SimpleMessage, 1=CallAndResponse, 2=MessageReply)
    uint8   status
    uint8   reserved
    uint32  sequenceId
    uint16  messageId     (2=MSG_ROUTE, 3=MSG_IDENTIFICATION)
    uint16  reserved
    uint32  messageLength

Overlapped (async) I/O is required so the recv thread and RPC callers can
read and write the pipe handle concurrently without deadlocking.
"""

import asyncio
import json
import logging
import os
import struct
import threading
import time
import uuid
from typing import Optional

import pywintypes
import win32api
import win32event
import win32file
import win32pipe

from proto import Routing_pb2, Actor_pb2

log = logging.getLogger(__name__)

_HEADER_FMT  = "<BBBBIHHI"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 16
_READ_BUFFER = 65536  # 64 KB
_WRITE_TIMEOUT_MS = 5_000
_READ_TIMEOUT_MS  = 500   # short so recv loop can check _running


def _dict_to_variant(value) -> "Actor_pb2.Variant":
    """Convert a Python dict/list/scalar to lua_actor::Variant protobuf."""
    v = Actor_pb2.Variant()
    if isinstance(value, bool):
        v.boolean = value
    elif isinstance(value, (int, float)):
        v.number = float(value)
    elif isinstance(value, str):
        v.str = value
    elif isinstance(value, dict):
        for k, val in value.items():
            v.table.entries[str(k)].CopyFrom(_dict_to_variant(val))
    elif isinstance(value, (list, tuple)):
        for i, val in enumerate(value, start=1):
            v.table.arr[i].CopyFrom(_dict_to_variant(val))
    else:
        v.table.CopyFrom(Actor_pb2.Table())   # empty table for None / unknown
    return v


def _variant_to_dict(v: "Actor_pb2.Variant"):
    """Convert lua_actor::Variant protobuf to a plain Python value."""
    kind = v.WhichOneof("value")
    if kind == "number":
        n = v.number
        return int(n) if n == int(n) else n
    if kind == "boolean":
        return v.boolean
    if kind == "str":
        return v.str
    if kind == "table":
        result = {k: _variant_to_dict(val) for k, val in v.table.entries.items()}
        for idx, val in v.table.arr.items():
            result[idx] = _variant_to_dict(val)
        return result
    return None


class _ProtoVersion:
    V0 = 0


class _Mode:
    SIMPLE            = 0
    CALL_AND_RESPONSE = 1
    REPLY             = 2


class _MsgId:
    NULL           = 0
    ECHO           = 1
    ROUTE          = 2
    IDENTIFICATION = 3
    DROPPED        = 4


def _pack_header(mode: int, seq: int, msg_id: int, length: int, status: int = 0) -> bytes:
    return struct.pack(_HEADER_FMT, _ProtoVersion.V0, mode, status, 0, seq, msg_id, 0, length)


def _unpack_header(data: bytes) -> dict:
    v, mode, status, _, seq, msg_id, _, length = struct.unpack(_HEADER_FMT, data[:_HEADER_SIZE])
    return {"version": v, "mode": mode, "status": status,
            "seq": seq, "msg_id": msg_id, "length": length}


class MQNotConnectedError(Exception):
    pass


def _overlapped_read(handle, buf_size: int, timeout_ms: int) -> Optional[bytes]:
    """Blocking overlapped read with timeout. Returns bytes or None on timeout."""
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
        return bytes(buf[:n])
    finally:
        win32api.CloseHandle(ov.hEvent)


def _overlapped_write(handle, data: bytes, timeout_ms: int = _WRITE_TIMEOUT_MS):
    """Blocking overlapped write with timeout."""
    ov = pywintypes.OVERLAPPED()
    ov.hEvent = win32event.CreateEvent(None, True, False, None)
    try:
        try:
            win32file.WriteFile(handle, data, ov)
        except pywintypes.error as e:
            if e.winerror != 997:   # ERROR_IO_PENDING is expected
                raise
        rc = win32event.WaitForSingleObject(ov.hEvent, timeout_ms)
        if rc == win32event.WAIT_TIMEOUT:
            win32file.CancelIo(handle)
            raise TimeoutError("WriteFile timed out")
        win32file.GetOverlappedResult(handle, ov, False)
    finally:
        win32api.CloseHandle(ov.hEvent)


class MQActorClient:
    """Async-compatible Actor RPC client for MacroQuest."""

    def __init__(self, pipe_name: str, actor_name: str, actor_mailbox: str):
        self._pipe_name     = pipe_name
        self._actor_name    = actor_name
        self._actor_mailbox = actor_mailbox
        self._client_uuid   = str(uuid.uuid4())

        self._pipe: Optional[object] = None
        self._running = False
        self._write_lock = threading.Lock()

        # All known EQ clients keyed by UUID.
        # Each entry: {uuid, pid, account, server, character}
        self._mq_clients: dict[str, dict] = {}
        self._clients_lock = threading.Lock()

        self._seq      = 0
        self._seq_lock = threading.Lock()

        # seq -> (asyncio.Loop, asyncio.Future)
        self._pending: dict[int, tuple] = {}
        self._pending_lock = threading.Lock()

        self._recv_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self):
        """Connect to the MQ named pipe and register our identity."""
        log.info("Opening pipe %s", self._pipe_name)
        self._pipe = win32file.CreateFile(
            self._pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            win32file.FILE_FLAG_OVERLAPPED,   # required for concurrent R/W
            None,
        )
        log.info("Pipe opened, setting message mode")
        win32pipe.SetNamedPipeHandleState(
            self._pipe,
            win32pipe.PIPE_READMODE_MESSAGE,
            None,
            None,
        )
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True, name="mq-recv")
        self._recv_thread.start()
        log.info("Sending identity")
        self._send_identity()
        time.sleep(0.1)
        log.info("Requesting identities from MQ")
        self._request_identities()
        time.sleep(0.3)   # allow identity responses to arrive before first call
        log.info("Connected as '%s/%s'", self._actor_name, self._actor_mailbox)

    def disconnect(self):
        self._running = False
        if self._pipe:
            try:
                win32file.CloseHandle(self._pipe)
            except Exception:
                pass
            self._pipe = None
        self._fail_pending(MQNotConnectedError("Disconnected"))

    def is_connected(self) -> bool:
        return self._pipe is not None and self._running

    # ------------------------------------------------------------------
    # RPC
    # ------------------------------------------------------------------

    def list_clients(self) -> list[dict]:
        """Return all currently known EQ clients."""
        with self._clients_lock:
            return list(self._mq_clients.values())

    def get_client(self, character: str = "") -> Optional[dict]:
        """Return a specific client by character name, or the first available client."""
        with self._clients_lock:
            if not self._mq_clients:
                return None
            if not character:
                return next(iter(self._mq_clients.values()))
            name_lower = character.lower()
            for client in self._mq_clients.values():
                if client["character"].lower() == name_lower:
                    return client
            return None

    async def call(self, mailbox: str, message: dict, timeout: float = 10.0,
                   character: str = "") -> dict:
        """Send an RPC request to a Lua Actor mailbox and await the response.

        Args:
            mailbox:   Fully-qualified Lua actor mailbox (e.g. 'lua:mq-mcp:mq-mcp')
            message:   Request payload dict
            timeout:   Seconds to wait for reply
            character: Route to this character's EQ process. Defaults to first client.
        """
        if not self.is_connected():
            raise MQNotConnectedError("Not connected to MQ pipe")

        client = self.get_client(character)
        if not client:
            raise MQNotConnectedError(
                f"No EQ client found{f' for character {character!r}' if character else ''}"
            )

        seq    = self._next_seq()
        loop   = asyncio.get_running_loop()
        future = loop.create_future()

        with self._pending_lock:
            self._pending[seq] = (loop, future)

        envelope = Routing_pb2.Envelope()
        # Use UUID-only routing (no process.pid) — avoids AmbiguousRecipient in launcher.
        # UUID routes via the ELSE branch of ServerPostOffice::RouteFromConnection which
        # filters by UUID, guaranteeing exactly one matching identity (the EQ process).
        # Mailbox must be the fully-qualified name "lua:scriptname:mailboxname" so that
        # DeliverTo's exact unordered_map lookup finds the registered mailbox.
        # e.g. script mq-mcp.lua registering mailbox "mq-mcp" → "lua:mq-mcp:mq-mcp"
        envelope.address.mailbox = mailbox
        envelope.address.uuid    = client["uuid"]
        log.debug("Routing to character=%r uuid=%s mailbox=%s",
                  client["character"], client["uuid"], mailbox)
        envelope.return_address.name        = self._actor_name
        envelope.return_address.mailbox     = self._actor_mailbox
        envelope.return_address.process.pid = os.getpid()
        envelope.mode     = _Mode.CALL_AND_RESPONSE
        envelope.sequence = seq
        envelope.payload  = _dict_to_variant(message).SerializeToString()

        self._send_raw(_MsgId.ROUTE, _Mode.CALL_AND_RESPONSE, seq, envelope.SerializeToString())

        try:
            result_envelope = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            with self._pending_lock:
                self._pending.pop(seq, None)
            raise

        # Non-zero status means a routing or delivery error; payload is a plain string.
        if result_envelope.status != 0:
            err_str = result_envelope.payload.decode("utf-8", errors="replace") if result_envelope.payload else "(no detail)"
            raise RuntimeError(f"MQ routing error (status={result_envelope.status}): {err_str}")

        try:
            return _variant_to_dict(Actor_pb2.Variant.FromString(result_envelope.payload))
        except Exception as parse_exc:
            raw = result_envelope.payload
            hint = raw.decode("utf-8", errors="replace")[:200] if raw else "(empty)"
            raise RuntimeError(
                f"Failed to parse reply as Variant: {parse_exc!r}\n"
                f"  status={result_envelope.status} payload={hint!r}"
            ) from parse_exc

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            return self._seq

    def _send_raw(self, msg_id: int, mode: int, seq: int, payload: bytes, status: int = 0):
        header = _pack_header(mode, seq, msg_id, len(payload), status)
        with self._write_lock:
            _overlapped_write(self._pipe, header + payload)

    def _request_identities(self):
        """Send MSG_IDENTIFICATION with empty payload to request all registered identities."""
        self._send_raw(_MsgId.IDENTIFICATION, _Mode.SIMPLE, 0, b"")

    def _send_identity(self):
        ident             = Routing_pb2.Identification()
        ident.process.pid = os.getpid()
        ident.uuid        = self._client_uuid
        ident.name        = self._actor_name
        # Launcher parses MSG_IDENTIFICATION as raw Identification (not AddIdentity wrapper)
        self._send_raw(_MsgId.IDENTIFICATION, _Mode.SIMPLE, 0, ident.SerializeToString())

    def _recv_loop(self):
        while self._running:
            try:
                data = _overlapped_read(self._pipe, _READ_BUFFER, _READ_TIMEOUT_MS)
            except pywintypes.error as e:
                if self._running:
                    log.warning("Pipe read error: %s", e)
                    self._running = False
                    self._fail_pending(MQNotConnectedError(f"Pipe error: {e}"))
                break
            except Exception as e:
                if self._running:
                    log.exception("Recv loop error: %s", e)
                continue

            if data is None:
                continue   # read timeout — loop and check _running

            if len(data) < _HEADER_SIZE:
                log.warning("Short read: %d bytes", len(data))
                continue

            hdr     = _unpack_header(data)
            payload = data[_HEADER_SIZE: _HEADER_SIZE + hdr["length"]]

            log.debug("Recv: msg_id=%d mode=%d seq=%d len=%d",
                      hdr["msg_id"], hdr["mode"], hdr["seq"], hdr["length"])

            if hdr["msg_id"] == _MsgId.IDENTIFICATION:
                self._handle_identification(payload)
            elif hdr["msg_id"] == 1002:  # MSG_MAIN_PROCESS_LOADED
                self._handle_process_loaded(payload)
            elif hdr["msg_id"] == _MsgId.ROUTE:
                # The EQ client's RouteMessage always sends with wire-header mode=0,
                # so check the inner envelope mode instead of the outer header mode.
                inner_mode = self._peek_envelope_mode(payload)
                if inner_mode == _Mode.REPLY:
                    self._dispatch_reply(hdr, payload)
                else:
                    self._handle_incoming_route(hdr, payload)
            elif hdr["msg_id"] == _MsgId.DROPPED:
                log.warning("MSG_DROPPED: seq=%d payload=%s", hdr["seq"], payload.hex() if payload else "")
            else:
                log.debug("Unhandled msg: msg_id=%d mode=%d seq=%d payload=%s",
                          hdr["msg_id"], hdr["mode"], hdr["seq"], payload[:32].hex() if payload else "")

    @staticmethod
    def _peek_envelope_mode(payload: bytes) -> int:
        """Return the mode field from the inner Envelope proto, or -1 on failure."""
        try:
            env = Routing_pb2.Envelope()
            env.ParseFromString(payload)
            return env.mode
        except Exception:
            return -1

    def _handle_identification(self, payload: bytes):
        """Parse Identification messages from MQ to track all connected EQ clients."""
        if not payload:
            return
        try:
            ident = Routing_pb2.Identification()
            ident.ParseFromString(payload)
            pid = ident.process.pid
            if not pid or pid == os.getpid():
                return
            uid        = ident.uuid or ""
            has_client = ident.HasField('client')
            log.info("Identity: pid=%d uuid=%r name=%r has_client=%s",
                     pid, uid, ident.name, has_client)
            if has_client and uid:
                entry = {
                    "uuid":      uid,
                    "pid":       pid,
                    "account":   ident.client.account,
                    "server":    ident.client.server,
                    "character": ident.client.character,
                }
                with self._clients_lock:
                    self._mq_clients[uid] = entry
                log.info("EQ client: character=%r server=%r uuid=%r",
                         ident.client.character, ident.client.server, uid)
        except Exception as e:
            log.debug("Could not parse identification: %s", e)

    def _handle_process_loaded(self, payload: bytes):
        """MSG_MAIN_PROCESS_LOADED — MQ announces the injected process PID."""
        log.info("MSG_MAIN_PROCESS_LOADED payload (%d bytes): %s", len(payload), payload.hex())
        # Try all known message types to find the PID
        for proto_cls in (Routing_pb2.AddIdentity, Routing_pb2.Identification,
                          Routing_pb2.Envelope):
            try:
                msg = proto_cls()
                msg.ParseFromString(payload)
                log.info("Parsed as %s: %s", proto_cls.__name__, msg)
            except Exception:
                pass

    def _handle_incoming_route(self, hdr: dict, payload: bytes):
        """Log unsolicited incoming ROUTE messages (e.g. Lua→Python announces)."""
        try:
            envelope = Routing_pb2.Envelope()
            envelope.ParseFromString(payload)
            ra = envelope.return_address
            log.info("Incoming ROUTE: from_name=%r from_mailbox=%r payload=%r",
                     ra.name,
                     ra.mailbox,
                     envelope.payload[:200] if envelope.HasField("payload") else b"")
        except Exception as e:
            log.warning("Failed to parse incoming route: %s", e)

    def _dispatch_reply(self, hdr: dict, payload: bytes):
        try:
            envelope = Routing_pb2.Envelope()
            envelope.ParseFromString(payload)
        except Exception as e:
            log.warning("Failed to parse reply envelope: %s", e)
            return

        seq = envelope.sequence or hdr["seq"]
        with self._pending_lock:
            entry = self._pending.pop(seq, None)
        if entry is None:
            log.debug("Reply for unknown seq %d", seq)
            return

        loop, future = entry
        loop.call_soon_threadsafe(self._resolve_future, future, envelope)

    @staticmethod
    def _resolve_future(future: asyncio.Future, value):
        if not future.done():
            future.set_result(value)

    def _fail_pending(self, exc: Exception):
        with self._pending_lock:
            entries = list(self._pending.values())
            self._pending.clear()
        for loop, future in entries:
            loop.call_soon_threadsafe(future.set_exception, exc)
