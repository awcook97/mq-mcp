from google.protobuf import descriptor as _descriptor
from google.protobuf.message import Message

class Table(Message):
    class EntriesEntry(Message):
        key: str
        value: Variant
    class ArrEntry(Message):
        key: int
        value: Variant
    entries: dict[str, Variant]
    arr: dict[int, Variant]

class ImVec2(Message):
    x: float
    y: float

class ImVec4(Message):
    x: float
    y: float
    z: float
    w: float

class Variant(Message):
    number: float
    boolean: bool
    str: str
    table: Table
    imvec2: ImVec2
    imvec4: ImVec4
    def WhichOneof(self, oneof_group: str) -> str | None: ...

DESCRIPTOR: _descriptor.FileDescriptor
