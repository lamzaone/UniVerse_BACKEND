import base64
from urllib.parse import unquote
from fastapi import FastAPI, HTTPException, Depends, Body, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Annotated, List, Dict, Optional
from sqlalchemy.orm import Session
import requests
from datetime import datetime, timedelta
from database import engine, SessionLocal
import secrets
import json
import models
from fastapi.middleware.cors import CORSMiddleware
import logging
import os
from fastapi.responses import FileResponse
from motor.motor_asyncio import AsyncIOMotorClient  # MongoDB async client
from bson import ObjectId

models.Base.metadata.create_all(bind=engine)

app = FastAPI()
CLIENT_ID = "167769953872-b5rnqtgjtuhvl09g45oid5r9r0lui2d6.apps.googleusercontent.com"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def generate_token():
    return secrets.token_urlsafe(32)

def generate_refresh_token():
    refresh_token = secrets.token_urlsafe(64)
    return refresh_token

db_dependency = Annotated[Session, Depends(get_db)]

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
    server_id: int
    position: int


class ServerCreate(BaseModel):
    name: str
    description: str
    owner_id: int


# WebSocket Connections Manager
class WebSocketManager:
    def __init__(self):
        self.main_connections: List[WebSocket] = []
        self.server_connections: Dict[int, List[WebSocket]] = {}  # server_id to list of WebSockets
        self.textroom_connections: Dict[int, Dict[int, List[WebSocket]]] = {}  # server_id to room_id to list of WebSockets

    # Handle connections for the main server
    async def connect_main(self, websocket: WebSocket):
        await websocket.accept()
        self.main_connections.append(websocket)

    def disconnect_main(self, websocket: WebSocket):
        self.main_connections.remove(websocket)

    async def broadcast_main(self, message: str):
        for connection in self.main_connections:
            await connection.send_text(message)

    # Handle connections for specific servers
    async def connect_server(self, websocket: WebSocket, server_id: int):
        await websocket.accept()
        if server_id not in self.server_connections:
            self.server_connections[server_id] = []
        self.server_connections[server_id].append(websocket)

    def disconnect_server(self, websocket: WebSocket, server_id: int):
        if server_id in self.server_connections:
            self.server_connections[server_id].remove(websocket)

    async def broadcast_server(self, server_id: int, message: str):
        if server_id in self.server_connections:
            for connection in self.server_connections[server_id]:
                await connection.send_text(message)

    # Handle connections for text rooms
    async def connect_textroom(self, websocket: WebSocket, room_id: int):
        await websocket.accept()
        if room_id not in self.textroom_connections:
            self.textroom_connections[room_id] = []
        self.textroom_connections[room_id].append(websocket)

    def disconnect_textroom(self, websocket: WebSocket, room_id: int):
        if room_id in self.textroom_connections:
            self.textroom_connections[room_id].remove(websocket)
            if not self.textroom_connections[room_id]:
                del self.textroom_connections[room_id]

    async def broadcast_textroom(self, room_id: int, message: str):
        if room_id in self.textroom_connections:
            for connection in self.textroom_connections[room_id]:
                await connection.send_text(message)

    # async def broadcastConnections(self, update_message: str, user_id: int, servers: List[int], friends: List[int] = None):
    #     # Broadcast to friends on the main server
    #     if friends:
    #         for connection in self.main_connections:
    #             if connection.user_id in friends:
    #                 try:
    #                     await connection.send_text(f"{user_id}: {update_message}")
    #                 except WebSocketDisconnect:
    #                     # Log and remove disconnected connection
    #                     print(f"Friend {connection.user_id} disconnected")
    #                     self.main_connections.remove(connection)
    #                 except Exception as e:
    #                     # Catch other exceptions
    #                     print(f"Error sending message to friend {connection.user_id}: {e}")

    #     # Broadcast to users in the same servers
    #     for server_id in servers:
    #         try:
    #             if server_id in self.server_connections:
    #                 for connection in self.server_connections[server_id]:
    #                     try:
    #                         await connection.send_text(f"{user_id}: {update_message}")
    #                     except WebSocketDisconnect:
    #                         # Log and remove disconnected connection
    #                         print(f"User {connection.user_id} in server {server_id} disconnected")
    #                         self.server_connections[server_id].remove(connection)
    #                     except Exception as e:
    #                         # Catch other exceptions
    #                         print(f"Error sending message to server {server_id}, user {connection.user_id}: {e}")
    #         except Exception as e:
    #             print(f"Error processing server {server_id}: {e}")

    
    async def broadcastConnections(self,update_message:str, user_id: int, servers: List[int], friends: List[int] = None):
        # Broadcast to friends on the main server
        try:
            if friends:
                for connection in self.main_connections:
                    if connection.user_id in friends:
                        await connection.send_text(f"{user_id}: {update_message}")

            # Broadcast to users in the same servers
            for server_id in servers:
                if server_id in self.server_connections:
                    for connection in self.server_connections[server_id]:
                        await connection.send_text(f"{user_id}: {update_message}")
        except Exception as e:
            print(f"Error broadcasting connections: {e}")
            pass







websocket_manager = WebSocketManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1.nip.io:4200", "https://www.coldra.in"],  # Adjust this to match your frontend's URL
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, PUT, DELETE, OPTIONS, etc.)
    allow_headers=["*"],  # Allow all headers
)

MONGO_DATABASE_URL = "mongodb://127.0.0.1:27017"
mongo_client = AsyncIOMotorClient(MONGO_DATABASE_URL)
mongo_db = mongo_client.uniVerse



IMAGE_DIR = "user_images"  # Directory to store user images
os.makedirs(IMAGE_DIR, exist_ok=True)



def generate_token():
    return secrets.token_urlsafe(64)

def generate_refresh_token():
    return secrets.token_urlsafe(64)


# Save profile picture to the filesystem
def save_image_to_filesystem(image_url: str, filename: str) -> str:
    """Download image from URL and save it to the filesystem."""
    response = requests.get(image_url)
    if response.status_code == 200:
        file_path = os.path.join(IMAGE_DIR, filename)
        with open(file_path, "wb") as f:
            f.write(response.content)
        return file_path
    else:
        logging.error(f"Failed to fetch image from URL: {image_url}")
        raise HTTPException(status_code=400, detail="Failed to retrieve user picture")

# Get user profile picture from the filesystem
@app.get("/api/images/{image_name}")
async def serve_image(image_name: str):
    image_name = unquote(image_name)
    """Endpoint to serve user images."""
    file_path = os.path.join(IMAGE_DIR, image_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)


# Google authentication
@app.post("/api/auth/google", response_model=User)
def google_auth(token_request: UserIn, db: db_dependency):
    # Verify the ID token
    id_token_response = requests.get(
        'https://www.googleapis.com/oauth2/v3/tokeninfo',
        params={'id_token': token_request.id_token}
    )
    
    if id_token_response.status_code != 200:
        logging.error(f"Google token validation failed: {id_token_response.text}")
        raise HTTPException(status_code=400, detail="Invalid token")

    google_data = id_token_response.json()
    email = google_data.get('email')
    if not email:
        raise HTTPException(status_code=400, detail="Invalid token: no email found")

    userinfo_response = requests.get(
        'https://www.googleapis.com/oauth2/v3/userinfo',
        headers={'Authorization': f'Bearer {token_request.access_token}'}
    )

    if userinfo_response.status_code != 200:
        logging.error(f"Failed to get user info: {userinfo_response.text}")
        raise HTTPException(status_code=400, detail="Failed to retrieve user information")

    user_info = userinfo_response.json()
    name = user_info.get('name')


    db_user = db.query(models.User).filter(models.User.email == email).first()
    if not db_user:
        # Create a new user if they don't exist
        picture_url = user_info.get('picture')

        # Save user picture to filesystem and store the path in the database
        picture_filename = f"{email}_profile.png"  # You can adjust the filename format as needed
        picture_path = save_image_to_filesystem(picture_url, picture_filename)
        refresh_token = generate_refresh_token()
        db_user = models.User(
            email=email,
            name=name,
            nickname=name,
            picture=picture_filename,  # Store only the filename in the database
            token=token_request.id_token,
            refresh_token=refresh_token,
            token_expiry=datetime.now() + timedelta(days=1),
            refresh_token_expiry=datetime.now() + timedelta(days=7)
        )
        db.add(db_user)
    else:
        db_user.token = token_request.id_token
        db_user.token_expiry = datetime.now() + timedelta(days=1)
        db_user.refresh_token_expiry = datetime.now() + timedelta(days=7)

    db.commit()
    db.refresh(db_user)

    # Create the user response
    db_user_data = {
        "id": db_user.id,
        "email": db_user.email,
        "name": db_user.name,
        "nickname": db_user.nickname,
        "picture": f"http://127.0.0.1.nip.io:8000/api/images/{db_user.picture}",  # Provide URL for frontend
        "token": db_user.token,
        "refresh_token": db_user.refresh_token
    }
    
    return db_user_data

# Refresh token
@app.post("/api/auth/refresh", response_model=User)
def refresh_tokens(token_request: TokenRequest, db: db_dependency):
    db_user = db.query(models.User).filter(models.User.refresh_token == token_request.token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid refresh token")
    
    if db_user.refresh_token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Refresh token expired")

    db_user.token = generate_token()
    db_user.token_expiry = datetime.now() + timedelta(days=1)
    db.commit()
    db.refresh(db_user)
    
    # Convert binary picture to base64 string for the response
    user_response = User(
        id=db_user.id,
        email=db_user.email,
        name=db_user.name,
        nickname=db_user.nickname,
        picture=f"http://127.0.0.1.nip.io:8000/api/images/{db_user.picture}",
        token=db_user.token,
        refresh_token=db_user.refresh_token,
    )
    
    return user_response

# Validate token
@app.post("/api/auth/validate", response_model=User)
def validate_token(token_request: TokenRequest, db: db_dependency):
    db_user = db.query(models.User).filter(models.User.token == token_request.token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid token")
    
    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")
    
    # Convert binary picture to base64 string for the response
    user_response = User(
        id=db_user.id,
        email=db_user.email,
        name=db_user.name,
        nickname=db_user.nickname,
        picture=f"http://127.0.0.1.nip.io:8000/api/images/{db_user.picture}",
        token=db_user.token,
        refresh_token=db_user.refresh_token,
    )
    
    return user_response


@app.post("/api/users/info", response_model = List[User])
async def get_users_info(user_ids: List[int], db: db_dependency): #needs a list of user ids, can be used for single ID as well
    users = await db.query(User).filter(User.id.in_(user_ids)).all()
    if not users:
        raise HTTPException(status_code=404, detail="Users not found")
    
    users_response = []
    for user in users:
        user_response = User(
            id=user.id,
            email=user.email,
            name=user.name,
            nickname=user.nickname,
            picture=f"http://127.0.0.1.nip.io:8000/api/images/{user.picture}",
            token=user.token,
            refresh_token=user.refresh_token,
        )
        users_response.append(user_response)
        
    return users_response


@app.get("/api/user/{user_id}", response_model=User)
def get_user(user_id: int, db: db_dependency):
    db_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    
        # Convert binary picture to base64 string for the response
    user_response = User(
        id=db_user.id,
        email=db_user.email,
        name=db_user.name,
        nickname=db_user.nickname,
        picture=f"http://127.0.0.1.nip.io:8000/api/images/{db_user.picture}",
        token=db_user.token,
        refresh_token=db_user.refresh_token,
    )
    
    return user_response

# Create a new server
@app.post("/api/server/create", response_model=Server)
def create_server(server: ServerCreate, db: db_dependency):
    db_server = models.Server(
        name=server.name,
        description=server.description,
        owner_id=server.owner_id
    )
    db_server.invite_code=secrets.token_urlsafe(4),
    db_server.created_at=datetime.now()

    
    db.add(db_server)
    db.commit()
    db.refresh(db_server)
    
    return db_server

# Get all servers of user
@app.get("/api/server/user/{user_id}", response_model=list[Server])
def get_servers(user_id: int, db: db_dependency):
    owned_servers = db.query(models.Server).filter(models.Server.owner_id == user_id).all()
    db_servers = db.query(models.Server).join(models.ServerMember).filter(models.ServerMember.user_id == user_id).all()
    db_servers.extend(owned_servers)
    return db_servers

@app.get("/api/server/{server_id}/online", response_model=List[int])
async def get_online_members(server_id: int, db: db_dependency):
    connected_users = await get_users_connected_server(server_id, db=db)
    return connected_users

@app.get("/api/server/{server_id}/users", response_model=List[int])
async def get_server_users(server_id: int, db: db_dependency):
    server_users = db.query(models.ServerMember).filter(models.ServerMember.server_id == server_id).all()
    owner_id = db.query(models.Server).filter(models.Server.id == server_id).first().owner_id
    user_ids = [user.user_id for user in server_users]
    user_ids.append(owner_id)
    return user_ids

# @app.get("/api/user/friends/", response_model=List[int])
# async def get_friends(user_id: int, db: db_dependency):
#     friends = db.query(models.Friend).filter(models.Friend.user_id == user_id).all()
#     return [friend.friend_id for friend in friends]

@app.put("/api/server/{server_id}/edit", response_model=Server)
async def edit_server(server_id: int, server_name: str, server_description: str, db: db_dependency):
    db_server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not db_server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    db_server.name = server_name
    db_server.description = server_description
    db.commit()
    db.refresh(db_server)
    
    # broadcast "server_updated" to all members
    await websocket_manager.broadcast_server(server_id, "server_updated")

    return db_server


# Get a server by ID
class GetServer(BaseModel):
    server_id: int
    user_id: int
@app.post("/api/server", response_model=Server)
def get_server(server_info: GetServer, db: db_dependency):
    try:
        db_server = db.query(models.Server).filter(models.Server.id == server_info.server_id).first()
        if not db_server:
            raise HTTPException(status_code=404, detail="Server not found")
        
        is_member = db.query(models.ServerMember).filter(
            models.ServerMember.user_id == server_info.user_id,
            models.ServerMember.server_id == server_info.server_id
        ).first()

        is_owner = db.query(models.Server).filter(
            models.Server.id == server_info.server_id,
            models.Server.owner_id == server_info.user_id
        ).first()
        
        # If the user is not a member, raise an exception
        if not is_member and not is_owner:
            raise HTTPException(status_code=403, detail="User is not a member of the server")
        
        # Return the server details if checks pass
        return db_server

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Join a server
class JoinServer(BaseModel):
    invite_code: str
    user_id: int
@app.post("/api/server/join", response_model=Server)
async def join_server(server_info: JoinServer, db: db_dependency):
    try:
        # Fetch the server using the invite code
        db_server = db.query(models.Server).filter(models.Server.invite_code == server_info.invite_code).first()
        
        if not db_server:
            raise HTTPException(status_code=404, detail="Server not found")
        
        # Check if the user is already a member of the server
        is_member = db.query(models.ServerMember).filter(
            models.ServerMember.user_id == server_info.user_id,
            models.ServerMember.server_id == db_server.id  # Correct filtering by server_id
        ).first()
        
        if is_member:
            raise HTTPException(status_code=400, detail="User is already a member of the server")
        
        # Add the user as a new member to the server
        db_member = models.ServerMember(
            user_id=server_info.user_id,
            server_id=db_server.id  # Use the server's ID from the fetched server
        )
        db.add(db_member)
        db.commit()

        await websocket_manager.broadcast_server(server_id=db_server.id, message=f"{server_info.user_id}: joined")

        # Return the server information
        return db_server
    
    except Exception as e:
        # Handle SQL-related errors
        print(e)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


class CategoryCreateRequest(BaseModel):
    category_name: str
@app.post("/api/server/{server_id}/category/create", response_model=RoomCategory)
async def create_category(server_id: int, category: CategoryCreateRequest, db: db_dependency):
    category_name = category.category_name
    category_position = db.query(models.RoomCategory).filter(models.RoomCategory.server_id == server_id).count()
    
    db_category = models.RoomCategory(
        name=category_name,
        server_id=server_id,
        position=category_position
    )
    
    db.add(db_category)
    db.commit()
    db.refresh(db_category)

    await websocket_manager.broadcast_server(server_id, "rooms_updated")
    
    return db_category

# Create a new room
@app.post("/api/server/{server_id}/room/create", response_model=ServerRoom)
async def create_room(server_id: int, room_name: str, room_type: str, db: db_dependency, category_id: int | None = None):
    # Calculate the next position for the new room
    if category_id == 0:
        category_id = None
    room_position = db.query(models.ServerRoom).filter(
        models.ServerRoom.server_id == server_id, 
        models.ServerRoom.category_id == category_id
    ).count() if category_id is not None else 0  # Handle None category_id case

    db_room = models.ServerRoom(
        name=room_name,
        type=room_type,
        server_id=server_id,
        category_id=category_id,
        position=room_position
    )
    
    db.add(db_room)
    db.commit()
    db.refresh(db_room)

    await websocket_manager.broadcast_server(server_id, "rooms_updated")
    return db_room

@app.put("/api/server/{server_id}/room/{room_id}/delete", response_model=str)
async def delete_room(server_id: int, room_id: int, db: db_dependency):
    db_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == room_id).first()
    if not db_room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    db.delete(db_room)
    db.commit()

    await websocket_manager.broadcast_server(server_id, "rooms_updated")
    return f"Room {room_id} has been deleted"

@app.put("/api/server/{server_id}/category/{category_id}/delete", response_model=str)
async def delete_category(server_id: int, category_id: int, db: db_dependency):
    db_category = db.query(models.RoomCategory).filter(models.RoomCategory.id == category_id).first()
    if not db_category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    db.delete(db_category)
    db.commit()

    await websocket_manager.broadcast_server(server_id, "rooms_updated")
    return f"Category {category_id} has been deleted"

class AccessIn(BaseModel):
    token: str
    server_id: int

@app.post("/api/server/access", response_model=int)
async def check_access(access_info: AccessIn, db: db_dependency):
    user = db.query(models.User).filter(models.User.token == access_info.token).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    #get "access_level"
    member = db.query(models.ServerMember).filter(models.ServerMember.user_id == user.id, models.ServerMember.server_id == access_info.server_id).first()
    if (member):
        return member.access_level if member.access_level else 0
    server_owner = db.query(models.Server).filter(models.Server.id == access_info.server_id).first().owner_id
    if user.id == server_owner:
        return 3
    else:
        raise HTTPException(status_code=404, detail="User not found in server")




class RoomReorder(BaseModel):
    room_id: int
    position: int
    category: Optional[int] = None


@app.post("/api/room/{room_id}/reorder", response_model=None)
async def reorder_room(new_info: RoomReorder, db: db_dependency):

    # If position == 0 and category is null, set the category to None so the room becomes uncategorized and return
    if (new_info.category is None):
        new_info.category = None 
        db_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == new_info.room_id).first()
        db_room.category_id = None
        db.commit()
        db.refresh(db_room)
        await websocket_manager.broadcast_server(db_room.server_id, "rooms_updated")
        return 

    # Get all rooms in the category
    db_rooms = db.query(models.ServerRoom).filter(models.ServerRoom.category_id == new_info.category).order_by(models.ServerRoom.position).all()
    # if not db_rooms:
    #     raise HTTPException(status_code=404, detail="No rooms found in the category")
    
    # Find the room to be reordered
    db_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == new_info.room_id).first()
    
    # If the category is changed
    if db_room.category_id != new_info.category:
        # Check if the new category exists
        if not db.query(models.RoomCategory).filter(models.RoomCategory.id == new_info.category).first():
            raise HTTPException(status_code=404, detail="Category not found")

        # Remove the room from the old category and insert it into the new category
        db_room.category_id = new_info.category
        # get all rooms in the new category ordered by position for further processing below (*)
        db_rooms = db.query(models.ServerRoom).filter(models.ServerRoom.category_id == new_info.category).order_by(models.ServerRoom.position).all()
        db_rooms.insert(new_info.position, db_room) # (*) insert the room into the new category at the new position
        for index, room in enumerate(db_rooms):     # (*) update the position of all rooms in the new category to reflect the new order
            room.position = index                   
        db.commit()
    
    # If the category is the same
    else:
        db_rooms.remove(db_room)                        # remove the room from the list of rooms in the category to insert it back at the new position
        db_rooms.insert(new_info.position, db_room)     # insert the room into the new position
        for index, room in enumerate(db_rooms):         
            room.position = index
        db.commit()

    await websocket_manager.broadcast_server(db_room.server_id, "rooms_updated")


# Fetch Categories and Rooms
class RoomResponse(BaseModel):
    id: int
    name: str
    type: str
    category_id: Optional[int]
    position: int

    class Config:
        from_attributes=True

class CategoryResponse(BaseModel):
    id: Optional[int]
    name: str
    position: int
    rooms: List[RoomResponse] = []

    class Config:
        from_attributes=True

# Endpoint to get categories and rooms for a server
@app.get("/api/server/{server_id}/categories", response_model=List[CategoryResponse])
def get_categories_and_rooms(server_id: int, db: Session = Depends(get_db)):
    # Retrieve all categories for the given server
    categories = (
        db.query(models.RoomCategory)
        .filter(models.RoomCategory.server_id == server_id)
        .order_by(models.RoomCategory.position)
        .all()
    )
    
    # Retrieve all rooms and group them by their category
    rooms = db.query(models.ServerRoom).filter(models.ServerRoom.server_id == server_id).all()
    room_map = {}
    for room in rooms:
        if room.category_id not in room_map:
            room_map[room.category_id] = []
        room_map[room.category_id].append(room)

    # Convert RoomCategory and ServerRoom instances to dictionaries or Pydantic models
    result = []
    for category in categories:
        category_dict = {
            'id': category.id,
            'name': category.name,
            'position': category.position,
            'rooms': []
        }
        category_rooms = [RoomResponse.model_validate(room) for room in room_map.get(category.id, [])]
        category_dict['rooms'] = category_rooms
        result.append(CategoryResponse(**category_dict))

    # Also add uncategorized rooms (rooms with category_id = None)
    uncategorized_rooms = [RoomResponse.model_validate(room) for room in room_map.get(None, [])]
    # if uncategorized_rooms:
    result.append(CategoryResponse(
        id=None, 
        name="Uncategorized", 
        position=len(result), 
        rooms=uncategorized_rooms
    ))

    return result

# FastAPI WebSocket management already handles connections for main, server, and text rooms. 


@app.websocket("/api/ws/main/{user_id}")
async def websocket_main_endpoint(websocket: WebSocket, user_id: int, db: db_dependency):
    """Handle WebSocket connections for the main server."""
    websocket.user_id = user_id
    await websocket_manager.connect_main(websocket)         # Conect to socket
    await broadcast_status(user_id,"online", db)            # Broadcast status to all servers
    try:
        while True:
            data = await websocket.receive_text()
            # Handle main server messages or updates
            await websocket_manager.broadcast_main(f"Main Server Update for User {user_id}: {data}")
    except WebSocketDisconnect:
        try:
            await broadcast_status(user_id,"offline", db)       # Broadcast status to all servers
        except Exception as e:
            pass
        websocket_manager.disconnect_main(websocket)        # Disconnect from socket

    except Exception as e:
        try:
            await broadcast_status(user_id,"offline", db)       # Broadcast status to all servers
        except Exception as e:
            pass
        websocket_manager.disconnect_main(websocket)

async def broadcast_status(user_id,status:str, db: db_dependency):
    servers = db.query(models.ServerMember).filter(models.ServerMember.user_id == user_id).all()       # Get all servers of user
    servers_owner = db.query(models.Server).filter(models.Server.owner_id == user_id).all()            # Get all servers owned by user
    servers = [server.server_id for server in servers]                                                 # Get all server ids from memberships
    servers.extend([server.id for server in servers_owner])                                            # Extend server ids with owned servers

    #TODO: Add friends
    friends = None
    await websocket_manager.broadcastConnections(status, user_id, servers, friends)


@app.websocket("/api/ws/server/{server_id}/{user_id}")
async def websocket_server_endpoint(websocket: WebSocket, server_id: int, user_id: int):
    """Handle WebSocket connections for a specific server."""
    
    websocket.user_id = user_id
    await websocket_manager.connect_server(websocket, server_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle messages related to the server
            await websocket_manager.broadcast_server(server_id, f"Message from User {user_id}: {data}")
    except WebSocketDisconnect:
        websocket_manager.disconnect_server(websocket, server_id)
        await websocket_manager.broadcast_server(server_id, f"User {user_id} disconnected from server {server_id}")

@app.websocket("/api/ws/textroom/{room_id}/{user_id}")
async def websocket_textroom_endpoint(websocket: WebSocket, room_id: int, user_id: int):
    
    websocket.user_id = user_id
    """Handle WebSocket connections for a specific text room."""
    await websocket_manager.connect_textroom(websocket, room_id)
    # Notify about user joining the text room
    await websocket_manager.broadcast_textroom(room_id, f"User {user_id} joined text room {room_id}")
    try:
        while True:
            data = await websocket.receive_text()
            await websocket_manager.broadcast_textroom( room_id, f"Message from User {user_id} in Text Room {room_id}: {data}")
    except WebSocketDisconnect:
        websocket_manager.disconnect_textroom(websocket, room_id)
        await websocket_manager.broadcast_textroom( room_id, f"User {user_id} left text room {room_id}")


class Message(BaseModel):
    message: str
    user_token: str
    room_id: int
    is_private: bool
    reply_to: Optional[int] = None

    class Config:
        from_attributes = True

class MessageResponse(BaseModel):
    message: str
    room_id: int
    is_private: bool
    reply_to: Optional[int]
    user_id: int
    timestamp: datetime
    _id: str  # Use _id directly from MongoDB

class MessagesRetrieve(BaseModel):
    room_id: int
    user_token: str

@app.post("/api/message", response_model=MessageResponse)
async def store_message(message: Message, db: db_dependency) -> MessageResponse:
    # Get user from token
    db_user = db.query(models.User).filter(models.User.token == message.user_token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid user token")
    
    # check if user is part of the server or server owner
    # get server from room ID
    server = db.query(models.ServerRoom).filter(models.ServerRoom.id == message.room_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Room not found")
    server = db.query(models.Server).filter(models.Server.id == server.server_id).first()
    server_member = db.query(models.ServerMember).filter(models.ServerMember.user_id == db_user.id, models.ServerMember.server_id == server.id).first()
    server_owner = db.query(models.Server).filter(models.Server.id == server.id, models.Server.owner_id == db_user.id).first()
    if not server_member and not server_owner:
        raise HTTPException(status_code=409, detail="User is not part of the server")
    
    
    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")
    
    # Prepare the message data
    message_data = {
        "message": message.message,
        "user_id": db_user.id,
        "room_id": message.room_id,
        "is_private": message.is_private,
        "reply_to": message.reply_to,
        "timestamp": datetime.now(),
    }

    # Insert the message into the database
    result = await mongo_db.messages.insert_one(message_data)

    # Broadcast the new message to the text room
    await websocket_manager.broadcast_textroom(message_data["room_id"], "new_message")

    # Return the response including _id
    return MessageResponse(
        message=message_data["message"],
        room_id=message_data["room_id"],
        is_private=message_data["is_private"],
        reply_to=message_data["reply_to"],
        user_id=message_data["user_id"],
        timestamp=message_data["timestamp"],
        _id=str(result.inserted_id)  # Use _id from MongoDB
    )

@app.post("/api/messages/", response_model=List[MessageResponse])
async def get_messages(request: MessagesRetrieve, db: db_dependency):
    # Verify the user token
    db_user = db.query(models.User).filter(models.User.token == request.user_token).first()

    #get server from room id

    # TODO: FIX CHECKING IF ROOM EXISTS AND ADD SERVERID TO MAKE SURE YOU CANT ACCESS ROOMS FROM OTHER SERVERS
    server = db.query(models.ServerRoom).filter(models.ServerRoom.id == request.room_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Room not found")
    server_member = db.query(models.ServerMember).filter(models.ServerMember.user_id == db_user.id, models.ServerMember.server_id == server.server_id).first()
    server_owner = db.query(models.Server).filter(models.Server.id == server.server_id, models.Server.owner_id == db_user.id).first()
    if not server_member and not server_owner:
        raise HTTPException(status_code=409, detail="User is not part of the server")

    room = db.query(models.ServerRoom).filter(models.ServerRoom.id == request.room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid token")

    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")

    # Retrieve the last 100 messages from MongoDB in descending order
    messages = await mongo_db.messages.find({"room_id": request.room_id}).sort("timestamp", -1).limit(100).to_list(length=100)

    # Reverse the order of the messages
    messages.reverse()

    # Return messages directly with _id from MongoDB
    for message in messages:
        message['_id'] = str(message['_id'])  # Ensure _id is returned as a string

    return messages

async def get_users_connected_server(server_id: int, db: db_dependency) -> List[int]:
    connected_users = []
    # Look for all server member ids to see if they are connected to main socket
    server_members = db.query(models.ServerMember).filter(models.ServerMember.server_id == server_id).all()
    for server_member in server_members:
        if any(conn.user_id == server_member.user_id for conn in websocket_manager.main_connections):
            connected_users.append(server_member.user_id)
    owner_id = db.query(models.Server).filter(models.Server.id == server_id).first().owner_id
    if any(conn.user_id == owner_id for conn in websocket_manager.main_connections):
        connected_users.append(owner_id)

    return connected_users


import uvicorn
if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0"
    )

