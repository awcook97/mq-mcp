"""Python Actor client for MacroQuest's named pipe protocol.

Connects to \\.\pipe\mqpipe, registers as a named Actor, and makes
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

import win32file
import win32pipe
import pywintypes

from proto import Routing_pb2

log = logging.getLogger(__name__)

# Header format: packed, little-endian
# B  protoVersion
# B  mode
# B  status
# B  reserved
# I  sequenceId  (uint32)
# H  messageId   (uint16)
# H  reserved
# I  messageLength (uint32)
_HEADER_FMT  = "<BBBBIHHI"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 16

_READ_BUFFER = 65536  # 64 KB — well above the 4 KB MQ message cap


class _ProtoVersion:
    V0 = 0


class _Mode:
    SIMPLE         = 0
    CALL_AND_RESPONSE = 1
    REPLY          = 2


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


class MQActorClient:
    """Async-compatible Actor RPC client for MacroQuest."""

    def __init__(self, pipe_name: str, actor_name: str, actor_mailbox: str):
        self._pipe_name    = pipe_name
        self._actor_name   = actor_name
        self._actor_mailbox = actor_mailbox
        self._client_uuid  = str(uuid.uuid4())

        self._pipe: Optional[object] = None
        self._running = False

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
        """Connect to the MQ named pipe and register our identity.
        Raises pywintypes.error if MQ is not running."""
        self._pipe = win32file.CreateFile(
            self._pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
        win32pipe.SetNamedPipeHandleState(
            self._pipe,
            win32pipe.PIPE_READMODE_MESSAGE,
            None,
            None,
        )
        self._running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True, name="mq-recv")
        self._recv_thread.start()
        self._send_identity()
        log.info("Connected to MQ pipe as '%s/%s'", self._actor_name, self._actor_mailbox)

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

    async def call(self, mailbox: str, message: dict, timeout: float = 10.0) -> dict:
        """Send an RPC request to a Lua Actor mailbox and await the response."""
        if not self.is_connected():
            raise MQNotConnectedError("Not connected to MQ pipe")

        seq    = self._next_seq()
        loop   = asyncio.get_running_loop()
        future = loop.create_future()

        with self._pending_lock:
            self._pending[seq] = (loop, future)

        envelope = Routing_pb2.Envelope()
        envelope.address.mailbox        = mailbox
        envelope.return_address.name    = self._actor_name
        envelope.return_address.mailbox = self._actor_mailbox
        envelope.mode     = _Mode.CALL_AND_RESPONSE
        envelope.sequence = seq
        envelope.payload  = json.dumps(message).encode()

        self._send_raw(_MsgId.ROUTE, _Mode.CALL_AND_RESPONSE, seq, envelope.SerializeToString())

        try:
            result_envelope = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            with self._pending_lock:
                self._pending.pop(seq, None)
            raise

        return json.loads(result_envelope.payload)

    # ------------------------------------------------------------------
    # Internal send / recv
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            return self._seq

    def _send_raw(self, msg_id: int, mode: int, seq: int, payload: bytes, status: int = 0):
        header = _pack_header(mode, seq, msg_id, len(payload), status)
        win32file.WriteFile(self._pipe, header + payload)

    def _send_identity(self):
        ident = Routing_pb2.Identification()
        ident.process.pid = os.getpid()
        ident.uuid        = self._client_uuid
        ident.name        = self._actor_name

        add_id = Routing_pb2.AddIdentity()
        add_id.id.CopyFrom(ident)

        self._send_raw(_MsgId.IDENTIFICATION, _Mode.SIMPLE, 0, add_id.SerializeToString())

    def _recv_loop(self):
        while self._running:
            try:
                _, data = win32file.ReadFile(self._pipe, _READ_BUFFER)
            except pywintypes.error as e:
                if self._running:
                    log.warning("Pipe read error: %s — disconnecting", e)
                    self._running = False
                    self._fail_pending(MQNotConnectedError(f"Pipe error: {e}"))
                break
            except Exception as e:
                if self._running:
                    log.exception("Unexpected error in recv loop: %s", e)
                time.sleep(0.1)
                continue

            if len(data) < _HEADER_SIZE:
                continue

            hdr     = _unpack_header(data)
            payload = data[_HEADER_SIZE: _HEADER_SIZE + hdr["length"]]

            if hdr["mode"] == _Mode.REPLY and hdr["msg_id"] == _MsgId.ROUTE:
                self._dispatch_reply(hdr, payload)

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
            log.debug("Received reply for unknown seq %d", seq)
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
