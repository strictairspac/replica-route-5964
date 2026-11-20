from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BookingEvent(BaseModel):
    at_ms: int = Field(ge=0, le=86400000)
    kind: Literal['inspect', 'register', 'retry', 'release', 'replicate', 'node_down', 'node_up', 'replica_read']
    session: str | None = Field(default=None, min_length=1, max_length=60)
    room: str | None = Field(default=None, max_length=80)
    start_ms: int | None = Field(default=None, ge=0, le=86400000)
    end_ms: int | None = Field(default=None, ge=0, le=86400000)
    attempt: int | None = Field(default=None, ge=1, le=1000)
    node: str | None = Field(default=None, max_length=80)
    position: int | None = Field(default=None, ge=0, le=1000000000)
    delay_ms: int | None = Field(default=None, ge=0, le=3600000)

    @model_validator(mode='after')
    def valid_event(self):
        if self.kind in ('inspect', 'register', 'retry', 'release'):
            if not self.room:
                raise ValueError('机房事件需要room')
            if self.start_ms is None or self.end_ms is None:
                raise ValueError('机房事件需要start_ms和end_ms')
            if not self.start_ms < self.end_ms:
                raise ValueError('时段必须满足start_ms<end_ms')
            if self.kind in ('register', 'retry') and self.attempt is None:
                raise ValueError('登记需要attempt作为幂等号')
        if self.kind in ('register', 'retry', 'release', 'replica_read') and not self.session:
            raise ValueError('该事件需要session')
        if self.kind in ('replicate', 'node_down', 'node_up') and not self.node:
            raise ValueError('节点事件需要node')
        if self.kind == 'replicate' and (self.position is None or self.delay_ms is None):
            raise ValueError('replicate需要position和delay_ms')
        return self


class BookingRunInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    strategy: Literal['hold', 'recheck'] = 'recheck'
    rooms: list[str] = Field(min_length=1, max_length=12)
    events: list[BookingEvent] = Field(min_length=1, max_length=100)
    hold_ttl_ms: int = Field(default=600000, ge=0, le=3600000)
    wait_max_ms: int = Field(default=300000, ge=0, le=3600000)
    max_delay_ms: int = Field(default=100, ge=0, le=3600000)
    sticky_ms: int = Field(default=500, ge=0, le=3600000)

    @model_validator(mode='after')
    def chronological(self):
        if len(set(self.rooms)) != len(self.rooms):
            raise ValueError('机房名必须唯一')
        if any(not room.strip() for room in self.rooms):
            raise ValueError('机房名不能为空')
        if any(a.at_ms > b.at_ms for a, b in zip(self.events, self.events[1:])):
            raise ValueError('事件必须按at_ms非递减排列')
        names = set(self.rooms)
        for event in self.events:
            if event.kind in ('inspect', 'register', 'retry', 'release') and event.room not in names:
                raise ValueError('事件引用未知机房：' + str(event.room))
        return self
