from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

class TokenRequest(BaseModel):
    token: str

class User(BaseModel):
    id: int
    email: str
    name: str
    nickname: str
    picture: str
    token: str
    refresh_token: str

    class Config:
        from_attributes=True

class UserIn(BaseModel):
    id_token: str
    access_token: str

class Server(BaseModel):
    id: int
    name: str
    description: str
    owner_id: int
    invite_code: str
    created_at: datetime


class ServerRoom(BaseModel):
    id: int
    type: str
    server_id: int
    name: str
    category_id: Optional[int]
    position: int

    class Config:
        from_attributes=True

class ServerMember(BaseModel):
    user_id: int
    server_id: int
    access_level: int

class RoomCategory(BaseModel):
    id: int
    name: str
    category_type: str
    server_id: int
    position: int


class ServerCreate(BaseModel):
    name: str
    description: str
    owner_id: int

class Message(BaseModel):
    message: str
    user_token: str
    room_id: int
    is_private: bool
    reply_to: Optional[int] = None
    attachments: Optional[List[str]] = None  # List of file URLs or filenames

    class Config:
        from_attributes = True

class MessageResponse(BaseModel):
    message: str
    room_id: int
    is_private: bool
    reply_to: Optional[int]
    user_id: int
    timestamp: datetime
    attachments: Optional[List[str]] = None
    _id: str

class MessagesRetrieve(BaseModel):
    room_id: int
    user_token: str
