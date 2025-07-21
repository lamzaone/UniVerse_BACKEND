from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

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
    weeks: Optional[List["ServerWeek"]] = None  # List of ServerWeek objects

    class Config:
        from_attributes = True


class Attendance(BaseModel):
    id: int
    user_id: int
    server_id: int
    date: datetime
    status: str  # e.g., "present", "absent", "excused"
    week_id: int

    class Config:
        from_attributes = True


class ServerWeek(BaseModel):
    id: int
    server_id: int
    week_number: int  # Week number in the semester

    class Config:
        from_attributes = True
    id: int
    server_id: int
    week_number: int  # Week number in the semester

    class Config:
        from_attributes = True


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

# TODO: fix like in AssignmentResponse
class MessageResponse(BaseModel):    
    id: str = Field(..., alias="_id")
    message: str
    room_id: int
    is_private: bool
    reply_to: Optional[str] = None
    user_id: int
    timestamp: datetime
    attachments: Optional[List[str]] = None

    class Config:
        populate_by_name = True

class MessagesRetrieve(BaseModel):
    room_id: int



class Assignment(BaseModel):
    message: str
    user_token: str
    room_id: int
    is_private: bool
    reply_to: Optional[str] = None
    attachments: Optional[List[str]] = None  # List of file URLs or filenames

    class Config:
        from_attributes = True

class AssignmentResponse(BaseModel):
    id: str = Field(..., alias="_id")
    message: str
    room_id: int
    is_private: bool
    reply_to: Optional[str] = None
    user_id: int
    timestamp: datetime
    grade: Optional[float] = None
    attachments: Optional[List[str]] = None

    class Config:
        populate_by_name = True

class AssignmentsRetrieve(BaseModel):
    room_id: int
