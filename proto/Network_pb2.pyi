from google.protobuf import descriptor as _descriptor
from google.protobuf.message import Message
from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper
from proto import Routing_pb2

class MessageType(int):
    Route: MessageType
    Leader: MessageType
    Handshake: MessageType
    Response: MessageType

Route: MessageType
Leader: MessageType
Handshake: MessageType
Response: MessageType

class Identity(Message):
    uuid: str
    port: int

class Header(Message):
    type: MessageType
    length: int
    address: str
    port: int

class NetworkMessage(Message):
    add: Routing_pb2.AddIdentity
    drop: Routing_pb2.DropIdentity
    request: Routing_pb2.RequestIdentities
    routed: bytes
    def WhichOneof(self, oneof_group: str) -> str | None: ...

class Announce(Message):
    address: str
    port: int

DESCRIPTOR: _descriptor.FileDescriptor
