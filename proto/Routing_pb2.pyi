from google.protobuf import descriptor as _descriptor
from google.protobuf.message import Message

class Process(Message):
    pid: int

class Peer(Message):
    ip: str
    port: int

class Client(Message):
    account: str
    server: str
    character: str

class Identification(Message):
    process: Process
    peer: Peer
    uuid: str
    name: str
    client: Client
    def WhichOneof(self, oneof_group: str) -> str | None: ...

class Address(Message):
    process: Process
    peer: Peer
    name: str
    client: Client
    mailbox: str
    uuid: str
    def WhichOneof(self, oneof_group: str) -> str | None: ...

class AddIdentity(Message):
    id: Identification

class DropIdentity(Message):
    id: Identification

class RequestIdentities(Message):
    process: Process
    peer: Peer
    uuid: str
    def WhichOneof(self, oneof_group: str) -> str | None: ...

class Envelope(Message):
    address: Address
    return_address: Address
    mode: int
    status: int
    sequence: int
    payload: bytes

class NotifyLevel(int):
    Info: NotifyLevel
    Warning: NotifyLevel
    Error: NotifyLevel

Info: NotifyLevel
Warning: NotifyLevel
Error: NotifyLevel

class Notification(Message):
    title: str
    message: str
    level: NotifyLevel

DESCRIPTOR: _descriptor.FileDescriptor
