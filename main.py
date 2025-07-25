import base64
from urllib.parse import unquote
import uuid
from zoneinfo import ZoneInfo
from fastapi import FastAPI, File, HTTPException, Depends, Body, Request, UploadFile, WebSocket, WebSocketDisconnect, Header
from fastapi.staticfiles import StaticFiles
import jwt as pyjwt  # Ensure PyJWT is installed: pip install PyJWT
from pydantic import BaseModel, Field
from typing import Annotated, List, Dict, Optional, Set
from sqlalchemy.orm import Session
import requests
from datetime import datetime, timedelta, timezone
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
from basemodels import *
models.Base.metadata.create_all(bind=engine)

app = FastAPI()
CLIENT_ID = "167769953872-b5rnqtgjtuhvl09g45oid5r9r0lui2d6.apps.googleusercontent.com"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


db_dependency = Annotated[Session, Depends(get_db)]

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()



# WebSocket Connections Manager
class WebSocketManager:
    def __init__(self):
        self.main_connections: List[WebSocket] = []
        self.server_connections: Dict[int, List[WebSocket]] = {}
        self.textroom_connections: Dict[int, List[WebSocket]] = {}
        self.audiovideo_connections: Dict[int, Dict[int, WebSocket]] = {}  # room_id -> {user_id: WebSocket}
        self.audiovideo_voice_users: Dict[int, Set[int]] = {}
        self.audiovideo_sharingscreen_users: Dict[int, Set[int]] = {}
        self.audiovideo_camera_users: Dict[int, Set[int]] = {}



    # --- MAIN SOCKET ---
    async def connect_main(self, websocket: WebSocket):
        await websocket.accept()
        self.main_connections.append(websocket)

    def disconnect_main(self, websocket: WebSocket):
        self.main_connections.remove(websocket)

    async def broadcast_main(self, message: str):
        for connection in self.main_connections:
            await connection.send_text(message)

    # --- SERVER SOCKET ---
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

    # --- TEXT ROOM SOCKET ---
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

    ########### AUDIO/VIDEO ROOM WEB SOCKET (SIGNALING) ###########


    async def connect_audiovideo(self, websocket: WebSocket, room_id: int, user_id: int):

        websocket.user_id = user_id
        await websocket.accept()
        # Add the connection even if the user hasn't joined voice
        self.audiovideo_connections.setdefault(room_id, []).append(websocket)
        self.audiovideo_voice_users.setdefault(room_id, set())
        self.audiovideo_camera_users.setdefault(room_id, set())
        self.audiovideo_sharingscreen_users.setdefault(room_id, set())

        # Notify all (including sender) about the user joining (optional: skip if not "connected")
        await self.broadcast_audiovideo(
            room_id,
            json.dumps({"type": "user-joined", "user_id": user_id}),
        )

    def disconnect_audiovideo(self, websocket: WebSocket, room_id: int, user_id: int):
        if room_id in self.audiovideo_connections:
            if websocket in self.audiovideo_connections[room_id]:
                self.audiovideo_connections[room_id].remove(websocket)
            if room_id in self.audiovideo_voice_users:
                self.audiovideo_voice_users[room_id].discard(user_id)
            if not self.audiovideo_connections[room_id]:
                del self.audiovideo_connections[room_id]
                del self.audiovideo_voice_users[room_id]

    async def broadcast_audiovideo(self, room_id: int, message: str):
        if room_id in self.audiovideo_connections:
            disconnected = []
            for ws in self.audiovideo_connections[room_id]:
                try:
                    await ws.send_text(message)
                except RuntimeError:
                    disconnected.append(ws)
                except Exception as e:
                    print(f"Failed to send to websocket: {e}")
                    disconnected.append(ws)

            for ws in disconnected:
                self.audiovideo_connections[room_id].remove(ws)


    async def relay_webrtc_signal(self, room_id: int, to_user_id: int, message: dict):
        try:
            if room_id in self.connect_audiovideo_connections:
                user_map = self.connect_audiovideo_connections[room_id]
                if to_user_id in user_map:
                    await user_map[to_user_id].send_json(message)
        except Exception as e:
            print(f"Relay error: {e}")

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

@app.websocket("/api/ws/audiovideo/{room_id}/{user_id}")
async def websocket_audiovideo_endpoint(websocket: WebSocket, room_id: int, user_id: int):
    # 1) connect → broadcast user-joined to all, including the new user
    websocket.user_id = user_id
    await websocket_manager.connect_audiovideo(websocket, room_id, user_id)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            payload = msg.get("message")
            if "joined_call" in payload:
                websocket_manager.audiovideo_voice_users[room_id].add(user_id)
            if "left_call" in payload:
                websocket_manager.audiovideo_voice_users[room_id].discard(user_id)
            if "started_sharing_screen" in payload:
                websocket_manager.audiovideo_sharingscreen_users[room_id].add(user_id)
            if "stopped_sharing_screen" in payload:
                websocket_manager.audiovideo_sharingscreen_users[room_id].discard(user_id)
            if "camera_on" in payload:
                websocket_manager.audiovideo_camera_users[room_id].add(user_id)
            if "camera_off" in payload:
                websocket_manager.audiovideo_camera_users[room_id].discard(user_id)


            # 2) signal (offer/answer/candidate) → broadcast to all (you’ll filter client-side if needed)
            await websocket_manager.broadcast_audiovideo(room_id, payload)

    except WebSocketDisconnect:
        # 3) on disconnect → broadcast user-left to everyone        
        if user_id in websocket_manager.audiovideo_voice_users[room_id]:
            websocket_manager.audiovideo_voice_users[room_id].discard(user_id)
        if user_id in websocket_manager.audiovideo_sharingscreen_users[room_id]:
            websocket_manager.audiovideo_sharingscreen_users[room_id].discard(user_id)
        if user_id in websocket_manager.audiovideo_camera_users[room_id]:
            websocket_manager.audiovideo_camera_users[room_id].discard(user_id)
        await websocket_manager.broadcast_audiovideo(room_id, f"user_left_call:${user_id}")


        websocket_manager.disconnect_audiovideo(websocket, room_id, user_id)

async def get_users_connected_server(server_id: int, db: db_dependency) -> List[int]:
    connected_users = []
    # Look for all server member ids to see if they are connected to main socket
    # check if room type is audiovideo
    server_rooms = db.query(models.ServerRoom).filter(models.ServerRoom.server_id == server_id).all()
    for server_room in server_rooms:
        if server_room.type == "audio":
            connected_users.extend(websocket_manager.audiovideo_voice_users.get(server_room.id, []))
            # return connected_users
    
    server_members = db.query(models.ServerMember).filter(models.ServerMember.server_id == server_id).all()
    for server_member in server_members:
        if any(conn.user_id == server_member.user_id for conn in websocket_manager.main_connections):
            connected_users.append(server_member.user_id)
    owner_id = db.query(models.Server).filter(models.Server.id == server_id).first().owner_id
    if any(conn.user_id == owner_id for conn in websocket_manager.main_connections):
        connected_users.append(owner_id)

    return connected_users

@app.get("/api/server/{server_id}/users", response_model=List[int])
async def get_server_users(server_id: int, db: db_dependency):
    server_users = db.query(models.ServerMember).filter(models.ServerMember.server_id == server_id).all()
    owner_id = db.query(models.Server).filter(models.Server.id == server_id).first().owner_id
    user_ids = [user.user_id for user in server_users]
    user_ids.append(owner_id)
    return user_ids




websocket_manager = WebSocketManager()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://lamzaone.go.ro:4200"],  # your Angular origin
    allow_credentials=True,
    allow_methods=["*"],    # allow CORS for all methods
    allow_headers=["*"],    # allow CORS for all headers
    expose_headers=["*"],  # allow CORS for all headers
    # don't allow anything else
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

def get_current_user(db: db_dependency, Authorization: str = Header(...)):
    try:
        if not Authorization:
            logger.error("Authorization header missing")
            raise HTTPException(status_code=401, detail="Authorization header missing")
        if not Authorization.startswith("Bearer "):
            logger.error("Invalid Authorization header format")
            raise HTTPException(status_code=401, detail="Invalid Authorization header")
        token = Authorization.replace("Bearer ", "")
        if not token:
            logger.error("Token is empty")
            raise HTTPException(status_code=401, detail="Invalid token")
        user = db.query(models.User).filter(models.User.token == token).first()
        if not user:
            logger.error(f"No user found for token: {token}")
            raise HTTPException(status_code=401, detail="Invalid token")
        if user.token_expiry < datetime.now(tz=timezone.utc):
            logger.error(f"Token expired for user: {user.id}")
            raise HTTPException(status_code=401, detail="Token expired")
        return user
    except Exception as e:
        logger.error(f"Error in get_current_user: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Unauthorized: {str(e)}")

# Get user profile picture from the filesystem
@app.get("/api/images/{image_name}")
async def serve_image(image_name: str):
    """Endpoint to serve user images."""
    image_name = unquote(image_name)    # Decode the image name from url
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
        "picture": f"http://lamzaone.go.ro:8000/api/images/{db_user.picture}",  # Provide URL for frontend
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
        picture=f"http://lamzaone.go.ro:8000/api/images/{db_user.picture}",
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
        picture=f"http://lamzaone.go.ro:8000/api/images/{db_user.picture}",
        token=db_user.token,
        refresh_token=db_user.refresh_token,
    )
    
    return user_response



@app.post("/api/users/info", response_model=List[User])
async def get_users_info(request: Request, db: db_dependency):
    """
    Expects JSON body: { "userIds": [1, 2, 3] }
    """
    data = await request.json()
    user_ids = data.get("userIds")
    if not user_ids or not isinstance(user_ids, list):
        raise HTTPException(status_code=400, detail="userIds must be a list of integers")
    users = db.query(models.User).filter(models.User.id.in_(user_ids)).all()
    if not users:
        raise HTTPException(status_code=404, detail="Users not found")
    users_response = []
    for user in users:
        user_response = User(
            id=user.id,
            email=user.email,
            name=user.name,
            nickname=user.nickname,
            picture=f"http://lamzaone.go.ro:8000/api/images/{user.picture}",
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
        picture=f"http://lamzaone.go.ro:8000/api/images/{db_user.picture}",
        token=db_user.token,
        refresh_token=db_user.refresh_token,
    )
    
    return user_response

# Create a new server
@app.post("/api/server/create", response_model=Server)
async def create_server(server: ServerCreate, db: db_dependency):
    db_server = models.Server(
        name=server.name,
        description=server.description,
        owner_id=server.owner_id,
        invite_code=secrets.token_urlsafe(4),
        created_at=datetime.now()
    )


    # Add the server to the database
    db.add(db_server)
    db.commit()
    db.refresh(db_server)

    # Create the first week for the server
    first_week = models.ServerWeek(
        server_id=db_server.id,
        week_number=1
    )
    db.add(first_week)
    db.commit()
    db.refresh(first_week)

    # Add "absent" attendance for each member with access_level 0
    members = db.query(models.ServerMember).filter_by(server_id=db_server.id, access_level=0).all()
    for member in members:
        attendance = models.Attendance(
            user_id=member.user_id,
            server_id=db_server.id,
            date=datetime.now(),
            status="absent",
            week=first_week
        )
        db.add(attendance)
    db.commit()

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
class ServerWithAccessLevel(Server):
    access_level: int

@app.post("/api/server", response_model=ServerWithAccessLevel)
def get_server(server_info: GetServer, db: db_dependency, Authorization: Optional[str] = Header(None)):
    # extract user from token
    token = Authorization.split(" ")[1] if Authorization else None
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    db_user = db.query(models.User).filter(models.User.token == token).first()
    if not db_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user_id = db_user.id

    try:
        db_server = db.query(models.Server).filter(models.Server.id == server_info.server_id).first()
        if not db_server:
            raise HTTPException(status_code=404, detail="Server not found")
        
        is_member = db.query(models.ServerMember).filter(
            models.ServerMember.user_id == user_id,
            models.ServerMember.server_id == server_info.server_id
        ).first()

        is_owner = db.query(models.Server).filter(
            models.Server.id == server_info.server_id,
            models.Server.owner_id == user_id
        ).first()
        
        # get access level
        access_level = 0
        if is_member:
            access_level = is_member.access_level
        elif is_owner:
            access_level = 3
        # If the user is not a member, raise an exception
        if not is_member and not is_owner:
            raise HTTPException(status_code=403, detail="User is not a member of the server")
        
        # add weeks to the server
        weeks = db.query(models.ServerWeek).filter(models.ServerWeek.server_id == db_server.id).all()
        db_server.weeks = [week for week in weeks]  # Convert to list of ServerWeek objects
        # Create the response model with access level
        server_response = ServerWithAccessLevel(
            id=db_server.id,
            name=db_server.name,
            description=db_server.description,
            owner_id=db_server.owner_id,
            invite_code=db_server.invite_code,
            created_at=db_server.created_at,
            weeks=db_server.weeks,  # Include the weeks in the response
            access_level=access_level  # Add access level to the response
        )
        
        # Return the server details if checks pass
        return server_response

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/server/{server_id}/room/{room_id}", response_model=ServerRoom)
def get_room(server_id: int, room_id: int, db: db_dependency, authorization: Optional[str] = Header(None)):
    # extract user from token
    token = authorization.split(" ")[1] if authorization else None
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    db_user = db.query(models.User).filter(models.User.token == token).first()
    if not db_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user_id = db_user.id
    db_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == room_id).first()
    if not db_room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    is_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user_id,
        models.ServerMember.server_id == server_id
    ).first()
    is_owner = db.query(models.Server).filter(
        models.Server.id == server_id,
        models.Server.owner_id == user_id
    ).first()

    
    return db_room

# Join a server
class JoinServer(BaseModel):
    invite_code: str
@app.post("/api/server/join", response_model=Server)
async def join_server(server_info: JoinServer, db: db_dependency, Authorization: Optional[str] = Header(None)):
    try:
        # extract user from token
        token = Authorization.split(" ")[1] if Authorization else None
        if not token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        db_user = db.query(models.User).filter(models.User.token == token).first()
        if not db_user:
            raise HTTPException(status_code=401, detail="Unauthorized")
        user_id = db_user.id
        # Check if the invite code is provided
        if not server_info.invite_code:
            raise HTTPException(status_code=400, detail="Invite code is required")
        # Check if the user ID is provided
        

        # Fetch the server using the invite code
        db_server = db.query(models.Server).filter(models.Server.invite_code == server_info.invite_code).first()
        
        if not db_server:
            raise HTTPException(status_code=404, detail="Server not found")
        
        # Check if the user is already a member of the server
        is_member = db.query(models.ServerMember).filter(
            models.ServerMember.user_id == user_id,
            models.ServerMember.server_id == db_server.id  # Correct filtering by server_id
        ).first()
        
        if is_member:
            return db_server 
            
        
        # Add the user as a new member to the server
        db_member = models.ServerMember(
            user_id=user_id,
            server_id=db_server.id  # Use the server's ID from the fetched server
        )
        db.add(db_member)

        # initialize all the attendance to absent for all weeks
        weeks = db.query(models.ServerWeek).filter(models.ServerWeek.server_id == db_server.id).all()
        for week in weeks:
            attendance = models.Attendance(
                user_id=user_id,
                server_id=db_server.id,                
                date=datetime.now(),
                status="absent",
                week_id=week.id  # Use week.id instead of the entire week object
            )
            db.add(attendance)


        db.commit()

        await websocket_manager.broadcast_server(server_id=db_server.id, message=f"{db_user.id}: joined")

        # Return the server information
        return db_server
    
    except Exception as e:
        # Handle SQL-related errors
        print(e)
        raise HTTPException(status_code=500, detail="Internal Server Error") from e


class CategoryCreateRequest(BaseModel):
    category_name: str
    category_type: str
@app.post("/api/server/{server_id}/category/create", response_model=RoomCategory)
async def create_category(server_id: int, category: CategoryCreateRequest, db: db_dependency):
    category_name = category.category_name
    category_type = category.category_type
    category_position = db.query(models.RoomCategory).filter(models.RoomCategory.server_id == server_id).count()

    
    db_category = models.RoomCategory(
        name=category_name,
        category_type=category_type,
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

    # delete from mongoDB aswell
    collection_name = f"server_{server_id}_room_{room_id}"
    mongo_db.drop_collection(collection_name)

    await websocket_manager.broadcast_server(server_id, "rooms_updated")
    await websocket_manager.broadcast_textroom(room_id, "room_deleted")
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

class AccessesIn(BaseModel):
    token: str
    server_id: int

class AccessLevelOut(BaseModel):
    user_id: int
    access_level: int

@app.post("/api/server/accesses", response_model=List[AccessLevelOut])
async def get_all_access_levels(access_info: AccessesIn, db: db_dependency):
    user = db.query(models.User).filter(models.User.token == access_info.token).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    server = db.query(models.Server).filter(models.Server.id == access_info.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    members = db.query(models.ServerMember).filter(models.ServerMember.server_id == access_info.server_id).all()
    result = [AccessLevelOut(user_id=member.user_id, access_level=member.access_level if member.access_level else 0) for member in members]
    # Add owner with access_level 3 if not already in members
    if not any(m.user_id == server.owner_id for m in members):
        result.append(AccessLevelOut(user_id=server.owner_id, access_level=3))
    return result


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
    category_type: str
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
            'category_type': category.category_type,
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
        category_type="Normal",
        position=len(result), 
        rooms=uncategorized_rooms
    ))

    return result




@app.get("/api/room/{room_id}/users")
def get_voice_users(room_id: int):
    users = list(websocket_manager.audiovideo_voice_users.get(room_id, []))
    return {"userIds": users}


# mount upload folder
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# @app.post("/api/upload")
# async def upload_file(db: db_dependency, file: UploadFile = File(...)):
#     user_token = file.headers.get("user_token")
#     db_user = db.query(models.User).filter(models.User.token == user_token).first()
#     if not db_user:
#         raise HTTPException(status_code=400, detail="Invalid user token")
    
#     # check file size
#     if file.size > 100 * 1024 * 1024: 
#         raise HTTPException(status_code=400, detail="File too large")
    
#     filename = f"{uuid.uuid4()}_{file.filename}"
#     print(file.filename)
#     file_path = os.path.join(UPLOAD_DIR, filename)
#     with open(file_path, "wb") as f:
#         f.write(await file.read())
#     return {"url": f"/uploads/{filename}"}


from fastapi import Form, File, UploadFile, Depends
from typing import List, Optional

@app.post("/api/message", response_model=MessageResponse)
async def store_message(db: db_dependency,
    message: str = Form(...),
    
    user_token: str = Form(...),
    room_id: int = Form(...),
    is_private: bool = Form(...),
    reply_to: Optional[str] = Form(None),
    attachments: List[UploadFile] = File(default=[]),      # <— accept files here
) -> MessageResponse:
    # Get user from token
    db_user = db.query(models.User).filter(models.User.token == user_token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid user token")

    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")

    # Check room & membership (unchanged)…
    server_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == room_id).first()
    if not server_room:
        raise HTTPException(status_code=404, detail="Room not found")
    server = db.query(models.Server).filter(models.Server.id == server_room.server_id).first()
    # …membership checks as before…

    # Save uploaded files to disk and collect URLs
    file_urls: List[str] = []
    for upload in attachments:
        # ext = upload.filename.split(".")[-1]
        name = f"{uuid.uuid4().hex}_{upload.filename}"
        dest = os.path.join(UPLOAD_DIR, name)
        with open(dest, "wb") as out:
            out.write(await upload.read())
        file_urls.append(f"http://lamzaone.go.ro:8000/uploads/{name}")

    # Prepare message_data (include attachments URLs)
    message_data = {
        "message": message,
        "user_id": db_user.id,
        "room_id": room_id,
        "is_private": is_private,
        "reply_to": reply_to,
        "attachments": file_urls,
        "timestamp": datetime.now(),
    }

    # Insert into Mongo, broadcast, and return (unchanged)…
    collection = mongo_db[f"server_{server.id}_room_{room_id}"]
    result = await collection.insert_one(message_data)
    await websocket_manager.broadcast_textroom(room_id, "new_message")

    return MessageResponse(
        message=message_data["message"],
        room_id=message_data["room_id"],
        is_private=message_data["is_private"],
        reply_to=message_data["reply_to"],
        user_id=message_data["user_id"],
        timestamp=message_data["timestamp"],
        _id=str(result.inserted_id),
        attachments=message_data["attachments"],
    )

@app.post("/api/messages/", response_model=List[MessageResponse])
async def get_messages(request: MessagesRetrieve, db: db_dependency, authorization: Optional[str] = Header(None)):
    # Extract token from Authorization header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.replace("Bearer ", "")

    # Verify the user token
    db_user = db.query(models.User).filter(models.User.token == token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid token")

    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")

    # Get the server from the room ID
    room = db.query(models.ServerRoom).filter(models.ServerRoom.id == request.room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    server = db.query(models.Server).filter(models.Server.id == room.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Check if the user is a member of the server or the server owner
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == db_user.id, 
        models.ServerMember.server_id == server.id
    ).first()
    server_owner = db.query(models.Server).filter(
        models.Server.id == server.id, 
        models.Server.owner_id == db_user.id
    ).first()
    if not server_member and not server_owner:
        raise HTTPException(status_code=403, detail="User is not part of the server")

    # Generate the collection name based on server and room ID
    collection_name = f"server_{server.id}_room_{request.room_id}"

    # Retrieve the last 100 messages from the specific collection in MongoDB
    messages = await mongo_db[collection_name].find({}).sort("timestamp", -1).limit(100).to_list(length=100)

    # Reverse the order of the messages
    messages.reverse()

    # Convert MongoDB _id to string
    for message in messages:
        message['_id'] = str(message['_id'])

    return messages

@app.put("/api/message/edit", response_model=MessageResponse)
async def edit_message(
    db: db_dependency,
    message_id: str = Form(...),
    room_id: int = Form(...),
    message: str = Form(...),
    attachments: List[UploadFile] = File(default=[]),  # Accept files here
    Authorization: Optional[str] = Header(None),
):
    # Extract token from Authorization header
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    # Verify the user token
    db_user = db.query(models.User).filter(models.User.token == token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid token")
    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")
    # Get the message from MongoDB
    server_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == room_id).first()
    if not server_room:
        raise HTTPException(status_code=404, detail="Room not found")

    collection_name = f"server_{server_room.server_id}_room_{server_room.id}"
    collection = mongo_db[collection_name]
    message_data = await collection.find_one({"_id": ObjectId(message_id)})

    # check if user can edit the message
    if db_user.id != message_data["user_id"]:
        raise HTTPException(status_code=403, detail="User not authorized to edit this message")

    if not message_data:
        raise HTTPException(status_code=404, detail="Message not found")
    # Check if the user is authorized to edit the message
    db_user = db.query(models.User).filter(models.User.id == message_data["user_id"]).first()

    # Update the message content
    message_data["message"] = message

    # Save uploaded files to disk and collect URLs
    file_urls: List[str] = []
    for upload in attachments:
        name = f"{uuid.uuid4().hex}_{upload.filename}"
        dest = os.path.join(UPLOAD_DIR, name)
        with open(dest, "wb") as out:
            out.write(await upload.read())
        file_urls.append(f"http://lamzaone.go.ro:8000/uploads/{name}")

    # Add attachments URLs to the message data
    message_data["attachments"].extend(file_urls)

    # Update the message in MongoDB
    await collection.update_one({"_id": ObjectId(message_id)}, {"$set": message_data})

    # Broadcast the updated message
    room_id = message_data["room_id"]
    await websocket_manager.broadcast_textroom(room_id, "message_updated")

    return MessageResponse(
        message=message_data["message"],
        room_id=message_data["room_id"],
        is_private=message_data["is_private"],
        reply_to=message_data.get("reply_to"),
        user_id=message_data["user_id"],
        timestamp=message_data["timestamp"],
        _id=str(message_data["_id"]),
        attachments=message_data["attachments"],
    )


###################### ASSIGNMENTS ######################


@app.post("/api/assignment", response_model=AssignmentResponse)
async def store_message(db: db_dependency,
    message: str = Form(...),
    user_token: str = Form(...),
    room_id: int = Form(...),
    is_private: bool = Form(...),
    reply_to: Optional[str] = Form(None),
    attachments: List[UploadFile] = File(default=[]),      # <— accept files here
) -> AssignmentResponse:
    # Get user from token
    db_user = db.query(models.User).filter(models.User.token == user_token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid user token")

    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")

    # Check room & membership (unchanged)…
    server_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == room_id).first()
    if not server_room:
        raise HTTPException(status_code=404, detail="Room not found")
    # if datetime(server_room.name.split(" ")[1]) < datetime.now():
    #     raise HTTPException(status_code=400, detail="Assignment expired")
    # if timedelta(days=1) < datetime.now() - datetime(server_room.name.split(" ")[2]):
    #     raise HTTPException(status_code=400, detail="Assignment expired")

    server = db.query(models.Server).filter(models.Server.id == server_room.server_id).first()
    # …membership checks as before…

    # Save uploaded files to disk and collect URLs
    file_urls: List[str] = []
    for upload in attachments:
        # ext = upload.filename.split(".")[-1]
        name = f"{uuid.uuid4().hex}_{upload.filename}"
        dest = os.path.join(UPLOAD_DIR, name)
        with open(dest, "wb") as out:
            out.write(await upload.read())
        file_urls.append(f"http://lamzaone.go.ro:8000/uploads/{name}")

    # Prepare message_data (include attachments URLs)
    message_data = {
        "message": message,
        "user_id": db_user.id,
        "room_id": room_id,
        "is_private": is_private,
        "reply_to": reply_to,
        "attachments": file_urls,
        "grade": None,  # Assignments start with no grade
        "timestamp": datetime.now(),
    }

    # Insert into Mongo, broadcast, and return (unchanged)…
    collection = mongo_db[f"server_{server.id}_assignments_{room_id}"]
    result = await collection.insert_one(message_data)

    
    await websocket_manager.broadcast_textroom(room_id, "new_message")

    return AssignmentResponse(
        message=message_data["message"],
        room_id=message_data["room_id"],
        is_private=message_data["is_private"],
        reply_to=message_data["reply_to"],
        user_id=message_data["user_id"],
        timestamp=message_data["timestamp"],
        grade=message_data["grade"],
        _id=str(result.inserted_id),
        attachments=message_data["attachments"],
    )

@app.post("/api/assignments/", response_model=List[AssignmentResponse])
async def get_messages(request: AssignmentsRetrieve, db: db_dependency, authorization: Optional[str] = Header(None)):
    # Extract token from Authorization header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.replace("Bearer ", "")

    # Verify the user token
    db_user = db.query(models.User).filter(models.User.token == token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid token")

    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")

    # Get the server from the room ID
    room = db.query(models.ServerRoom).filter(models.ServerRoom.id == request.room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    server = db.query(models.Server).filter(models.Server.id == room.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Check if the user is a member of the server or the server owner
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == db_user.id, 
        models.ServerMember.server_id == server.id
    ).first()
    server_owner = db.query(models.Server).filter(
        models.Server.id == server.id, 
        models.Server.owner_id == db_user.id
    ).first()
    if not server_member and not server_owner:
        raise HTTPException(status_code=403, detail="User is not part of the server")

    # Generate the collection name based on server and room ID
    collection_name = f"server_{server.id}_assignments_{request.room_id}"

    # Retrieve the last 100 messages from the specific collection in MongoDB
    # messages = await mongo_db[collection_name].find({}).sort("timestamp", -1).limit(100).to_list(length=100)
    #check if db_user is server owner or level 2
    if server_owner or (server_member and server_member.access_level > 0):
        messages = await mongo_db[collection_name].find({}).sort("timestamp", -1).to_list(length=1000)
    else:
        messages = await mongo_db[collection_name].find({"user_id": db_user.id}).sort("timestamp", -1).to_list(length=1000)
        # Add messages from server owner or users with access_level > 0 that don't have a "reply_to" field
        elevated_user_ids = [
            member.user_id for member in db.query(models.ServerMember)
            .filter(
                models.ServerMember.server_id == server.id,
                models.ServerMember.access_level > 0
            ).all()
        ]

        user_message_ids = [str(msg["_id"]) for msg in messages if msg["user_id"] == db_user.id]

        messages.extend(
            await mongo_db[collection_name].find({
                "$and": [
                    {
                        "user_id": {
                            "$in": [server.owner_id] + elevated_user_ids
                        }
                    },
                    {
                        "$or": [
                            {"reply_to": "0"},
                            {"reply_to": {"$in": user_message_ids}}
                        ]
                    }
                ]
            }).sort("timestamp", -1).to_list(length=1000)
        )


    # messages = await mongo_db[collection_name].find({}).sort("timestamp", -1).to_list(length=1000)
    messages = sorted(messages, key=lambda msg: msg["timestamp"])

    for message in messages:
        message['_id'] = str(message['_id'])

    return messages
class GradeAssignment(BaseModel):
    assignment_id: str
    room_id: int
    grade: float

@app.put("/api/assignment/grade", response_model=AssignmentResponse)
async def grade_assignment(grade_assignment: GradeAssignment, db: db_dependency, authorization: Optional[str] = Header(None)):
    # Extract token from Authorization header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")
    # Verify the user token
    db_user = db.query(models.User).filter(models.User.token == token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid token")
    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")
    # Check if the user is a server owner or has access level > 0
    # Get the server from the room ID
    server_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == grade_assignment.room_id).first()
    if not server_room:
        raise HTTPException(status_code=404, detail="Room not found")

    server = db.query(models.Server).filter(models.Server.id == server_room.server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Check if the user is a server member with access level > 0 or the server owner
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == db_user.id,
        models.ServerMember.server_id == server.id,
        models.ServerMember.access_level > 0
    ).first()

    if not server_member and server.owner_id != db_user.id:
        raise HTTPException(status_code=403, detail="User is not authorized to grade assignments")
    # Get the assignment from MongoDB
    collection_name = f"server_{server.id}_assignments_{grade_assignment.room_id}"
    assignment = await mongo_db[collection_name].find_one({"_id": ObjectId(grade_assignment.assignment_id)})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    # Update the assignment grade
    assignment["grade"] = grade_assignment.grade
    await mongo_db[collection_name].update_one(
        {"_id": ObjectId(grade_assignment.assignment_id)},
        {"$set": {"grade": grade_assignment.grade}}
    )
    # Convert the assignment to the response model
    assignment_response = AssignmentResponse(
        id=grade_assignment.assignment_id,
        message=assignment["message"],
        room_id=grade_assignment.room_id,
        is_private=assignment["is_private"],
        reply_to=assignment["reply_to"],
        user_id=assignment["user_id"],
        timestamp=assignment["timestamp"],
        grade=assignment["grade"],
        attachments=assignment.get("attachments", [])
    )
    # Broadcast the updated assignment to the room
    await websocket_manager.broadcast_textroom(grade_assignment.room_id, "assignment_graded")
    return assignment_response

@app.put("/api/assignment/edit", response_model=AssignmentResponse)
async def edit_assignment(
    db: db_dependency,
    assignment_id: str = Form(...),
    room_id: int = Form(...),
    message: str = Form(...),
    attachments: List[UploadFile] = File(default=[]),
    authorization: Optional[str] = Header(None)
):
    # Extract token from Authorization header
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.replace("Bearer ", "")

    # Verify the user token
    db_user = db.query(models.User).filter(models.User.token == token).first()
    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid token")
    if db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")

    # Retrieve the server ID from the room
    server_room = db.query(models.ServerRoom).filter(models.ServerRoom.id == room_id).first()
    if not server_room:
        raise HTTPException(status_code=404, detail="Room not found")
    collection_name = f"server_{server_room.server_id}_assignments_{room_id}"
    assignment = await mongo_db[collection_name].find_one({"_id": ObjectId(assignment_id)})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if assignment["user_id"] != db_user.id:
        raise HTTPException(status_code=403, detail="You are not authorized to edit this assignment")

    # Save uploaded files to disk and collect URLs
    file_urls: List[str] = []
    for upload in attachments:
        name = f"{uuid.uuid4().hex}_{upload.filename}"
        dest = os.path.join(UPLOAD_DIR, name)
        with open(dest, "wb") as out:
            out.write(await upload.read())
        file_urls.append(f"http://lamzaone.go.ro:8000/uploads/{name}")

    # Update the assignment message and attachments
    await mongo_db[collection_name].update_one(
        {"_id": ObjectId(assignment_id)},
        {"$set": {"message": message, "attachments": file_urls}}
    )

    # Prepare the response
    assignment_response = AssignmentResponse(
        _id=str(assignment_id),
        message=message,
        room_id=room_id,
        is_private=assignment["is_private"],
        reply_to=assignment["reply_to"],
        user_id=assignment["user_id"],
        timestamp=assignment["timestamp"],
        grade=assignment.get("grade", None),
        attachments=file_urls
    )

    # Broadcast the updated assignment to the room
    await websocket_manager.broadcast_textroom(room_id, "assignment_edited")
    return assignment_response



#################################### ATTENDANCE #########################################
class AttendanceCreateRequest(BaseModel):
    user_id: int
    date: datetime
    status: str  # e.g., "present", "absent", "excused"
    week: int
class AttendanceEditRequest(BaseModel):
    attendance_id: int
    status: str  # e.g., "present", "absent", "excused"

@app.put("/api/server/{server_id}/attendance/edit", response_model=AttendanceCreateRequest)
async def edit_attendance(server_id: int, attendance_edit: AttendanceEditRequest, db: db_dependency, Authorization: Optional[str] = Header(None)):
    # Extract token from Authorization header
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    # Verify the user token
    admin = db.query(models.User).filter(models.User.token == token).first()
    if not admin:
        raise HTTPException(status_code=400, detail="Invalid user token")
    if admin.token_expiry < datetime.now():
        raise HTTPException(status_code=400, detail="Token expired")
    # Check if the user is server owner or has access level > 0
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == admin.id,
        models.ServerMember.server_id == server.id,
        models.ServerMember.access_level > 0
    ).first()
    if not server_member and server.owner_id != admin.id:
        raise HTTPException(status_code=403, detail="User is not authorized to edit attendance records")
    # Find the attendance record
    db_attendance = db.query(models.Attendance).filter(
        models.Attendance.id == attendance_edit.attendance_id,
        models.Attendance.server_id == server_id
    ).first()
    if not db_attendance:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    # Update the attendance record
    db_attendance.status = attendance_edit.status
    db.commit()
    db.refresh(db_attendance)
    # Broadcast the attendance record update
    await websocket_manager.broadcast_server(server_id, "attendance_updated")

    return AttendanceCreateRequest(
        user_id=db_attendance.user_id,
        date=db_attendance.date,
        status=db_attendance.status,
        week=db_attendance.week
    )
    


# delete last week endpoint
@app.delete("/api/server/{server_id}/weeks/delete")
async def delete_last_week(server_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    # Token validation
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    admin = db.query(models.User).filter(models.User.token == token).first()
    if not admin or admin.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # Authorization check
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == admin.id,
        models.ServerMember.server_id == server.id,
        models.ServerMember.access_level > 0
    ).first()
    if not server_member and server.owner_id != admin.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    # Fetch the last week
    last_week = db.query(models.ServerWeek).filter_by(server_id=server_id).order_by(models.ServerWeek.week_number.desc()).first()
    if not last_week:
        raise HTTPException(status_code=404, detail="No weeks found for this server")
    # Delete the last week
    db.query(models.ServerWeek).filter_by(id=last_week.id).delete()
    db.commit()
    # Delete all attendance records for the last week
    db.query(models.Attendance).filter_by(week_id=last_week.id).delete()
    db.commit()
    # Broadcast the week deletion
    await websocket_manager.broadcast_server(server_id, "week_deleted")
    return {"message": f"Week {last_week.week_number} deleted successfully."}


@app.post("/api/server/{server_id}/weeks/create")
async def create_week(server_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    # Token validation
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    admin = db.query(models.User).filter(models.User.token == token).first()
    if not admin or admin.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Authorization check
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == admin.id,
        models.ServerMember.server_id == server.id,
        models.ServerMember.access_level > 0
    ).first()
    if not server_member and server.owner_id != admin.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # get number of weeks already created for this server
    existing_weeks = db.query(models.ServerWeek).filter_by(server_id=server_id).count()
    # Create the week
    new_week = models.ServerWeek(server_id=server_id, week_number=existing_weeks + 1)
    db.add(new_week)
    db.commit()
    db.refresh(new_week)

    # Add "absent" attendance for each member with access_level 0
    members = db.query(models.ServerMember).filter_by(server_id=server_id, access_level=0).all()
    for member in members:
        attendance = models.Attendance(
            user_id=member.user_id,
            server_id=server_id,
            date=datetime.now(),
            status="absent",
            week_id=new_week.id  # Use week_id instead of passing the entire object
        )
        db.add(attendance)
    db.commit()
    
    # Broadcast the new week creation
    await websocket_manager.broadcast_server(server_id, "week_created")

    return {"message": f"Week {new_week.week_number} created and attendance set to 'absent' for all members."}

# TODO: Fix /weeks endpoint
@app.get("/api/server/{server_id}/weeks")
async def get_weeks(server_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    # Token validation
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # Authorization check
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id,
        models.ServerMember.server_id == server.id
    ).first()
    if not server_member and server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    # Fetch weeks
    weeks = db.query(models.ServerWeek).filter_by(server_id=server_id).all()
    result = [{"id": week.id, "week_number": week.week_number} for week in weeks]
    return result



@app.get("/api/server/{server_id}/week/{week_number}/attendance")
async def get_attendance_for_week(server_id: int, week_number: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    # Token check
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Validate access
    server = db.query(models.Server).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server_member = db.query(models.ServerMember).filter_by(server_id=server_id, user_id=user.id).first()
    if not server_member and server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Fetch week and attendance
    week = db.query(models.ServerWeek).filter_by(server_id=server_id, week_number=week_number).first()
    if not week:
        raise HTTPException(status_code=404, detail="Week not found")

    attendance_records = db.query(models.Attendance).filter_by(server_id=server_id, week_id=week.id).all()

    for a in attendance_records:
        # Count total attendances ('present' + 'excused') for the user in the given server
        a.total = db.query(models.Attendance).filter(
            models.Attendance.server_id == server_id,
            models.Attendance.user_id == a.user_id,
            models.Attendance.status.in_(["present", "excused"])
        ).count()

    # Prepare the response
    result = [{
        "user_id": a.user_id,
        "user_name": a.user.name,
        "status": a.status,
        "date": a.date,
        "attendance_id": a.id,
        "total": a.total if hasattr(a, 'total') else 0
    } for a in attendance_records]

    return {"week": week_number, "attendance": result}

@app.get("/api/server/{server_id}/weeks")
async def list_weeks(server_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    token = Authorization.replace("Bearer ", "") if Authorization else None
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    is_member = db.query(models.ServerMember).filter_by(server_id=server_id, user_id=user.id).first()
    if not is_member and server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    weeks = db.query(models.ServerWeek).filter_by(server_id=server_id).all()
    return [{"id": w.id, "week_number": w.week_number} for w in weeks]

@app.get("/api/server/{server_id}/user/{user_id}/attendance")
async def user_attendance(server_id: int, user_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    token = Authorization.replace("Bearer ", "") if Authorization else None
    requester = db.query(models.User).filter(models.User.token == token).first()
    if not requester or requester.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if requester.id != user_id:
        # Admin check
        server_member = db.query(models.ServerMember).filter_by(server_id=server_id, user_id=requester.id).first()
        if not server_member or server_member.access_level <= 0 and server.owner_id != requester.id:
            raise HTTPException(status_code=403, detail="Not authorized")

    attendance = db.query(models.Attendance).filter_by(server_id=server_id, user_id=user_id).all()
    return [{
        "week_number": a.week.week_number if a.week else None,
        "date": a.date,
        "status": a.status
    } for a in attendance]

class BulkAttendanceEditRequest(BaseModel):
    updates: List[AttendanceEditRequest]  # attendance_id + status

@app.put("/api/server/{server_id}/week/{week_number}/attendance/bulk_edit")
async def bulk_edit_attendance(server_id: int, week_number: int, request: BulkAttendanceEditRequest, db: db_dependency, Authorization: Optional[str] = Header(None)):
    token = Authorization.replace("Bearer ", "") if Authorization else None
    db_user = db.query(models.User).filter(models.User.token == token).first()
    if not db_user or db_user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    server = db.query(models.Server).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    member = db.query(models.ServerMember).filter_by(server_id=server_id, user_id=db_user.id).first()
    if member and member.access_level <= 0 or server.owner_id != db_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    for edit in request.updates:
        record = db.query(models.Attendance).filter_by(id=edit.attendance_id, server_id=server_id).first()
        if record:
            record.status = edit.status
            record.date = datetime.now()  # Update date to now
    db.commit()
    await websocket_manager.broadcast_server(server_id, "bulk_attendance_updated")
    return {"message": "Attendance updated."}

from fastapi.responses import StreamingResponse
import csv
import io

@app.get("/api/server/{server_id}/attendance/export")
async def export_attendance(server_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    token = Authorization.replace("Bearer ", "") if Authorization else None
    admin = db.query(models.User).filter(models.User.token == token).first()
    if not admin or admin.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    member = db.query(models.ServerMember).filter_by(server_id=server_id, user_id=admin.id).first()
    if not member or member.access_level <= 0 and server.owner_id != admin.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    records = db.query(models.Attendance).filter_by(server_id=server_id).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["User ID", "Week Number", "Date", "Status"])
    for rec in records:
        writer.writerow([rec.user_id, rec.week.week_number if rec.week else "", rec.date, rec.status])

    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=attendance.csv"})

@app.delete("/api/server/{server_id}/week/{week_number}")
async def delete_week(server_id: int, week_number: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    token = Authorization.replace("Bearer ", "") if Authorization else None
    admin = db.query(models.User).filter(models.User.token == token).first()
    if not admin or admin.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if admin.id != server.owner_id:
        member = db.query(models.ServerMember).filter_by(server_id=server_id, user_id=admin.id).first()
        if not member or member.access_level <= 0:
            raise HTTPException(status_code=403, detail="Not authorized")

    week = db.query(models.ServerWeek).filter_by(server_id=server_id, week_number=week_number).first()
    if not week:
        raise HTTPException(status_code=404, detail="Week not found")

    # Delete all related attendance first (cascading isn't configured here)
    db.query(models.Attendance).filter_by(server_id=server_id).filter(models.Attendance.week == week).delete()
    db.delete(week)
    db.commit()

    return {"message": f"Week {week_number} and related attendance deleted."}

@app.get("/api/server/{server_id}/attendance/full")
async def full_attendance(server_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    token = Authorization.replace("Bearer ", "") if Authorization else None
    admin = db.query(models.User).filter(models.User.token == token).first()
    if not admin or admin.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter_by(id=server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    member = db.query(models.ServerMember).filter_by(server_id=server_id, user_id=admin.id).first()
    if not member or member.access_level <= 0 and server.owner_id != admin.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Get all users in the server
    users = db.query(models.User.id, models.User.username).join(models.ServerMember, models.User.id == models.ServerMember.user_id)\
            .filter(models.ServerMember.server_id == server_id).all()

    # Get all weeks
    weeks = db.query(models.ServerWeek).filter_by(server_id=server_id).order_by(models.ServerWeek.week_number).all()

    # Get all attendance records
    attendance_records = db.query(models.Attendance).filter_by(server_id=server_id).all()

    # Build mapping (user_id -> week_number -> status)
    attendance_map = {}
    for rec in attendance_records:
        if rec.user_id not in attendance_map:
            attendance_map[rec.user_id] = {}
        attendance_map[rec.user_id][rec.week.week_number if rec.week else 0] = {
            "status": rec.status,
            "attendance_id": rec.id
        }

    # Structure response
    response = {
        "weeks": [{"id": w.id, "week_number": w.week_number} for w in weeks],
        "users": []
    }
    for user in users:
        user_attendance = {
            "id": user.id,
            "name": user.username,
            "attendance": {},  # week_number -> status
            "attendance_ids": {}  # week_number -> attendance_id
        }
        for week in weeks:
            record = attendance_map.get(user.id, {}).get(week.week_number)
            if record:
                user_attendance["attendance"][week.week_number] = record["status"]
                user_attendance["attendance_ids"][week.week_number] = record["attendance_id"]
            else:
                user_attendance["attendance"][week.week_number] = "absent"
        response["users"].append(user_attendance)

    return response



################### STUDENT GRADES ####################
class AddGradeRequest(BaseModel):
    user_id: int
    grade: float

class EditGradeRequest(BaseModel):
    user_id: int
    grade: float
    assignment_id: Optional[str] = None
    date: Optional[datetime] = None

@app.post("/api/server/{server_id}/admin/grade", response_model=List[dict])
async def add_student_grade(server_id: int, grade_request: AddGradeRequest, db: db_dependency, Authorization: Optional[str] = Header(None)):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id,
        models.ServerMember.server_id == server.id
    ).first()
    if not server.owner_id == user.id and (not server_member or server_member.access_level <= 0):
        raise HTTPException(status_code=403, detail="User is not authorized to add grades for this student")

    student_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == grade_request.user_id,
        models.ServerMember.server_id == server_id
    ).first()
    if not student_member:
        raise HTTPException(status_code=404, detail="Student not found in the server")

    grades = {}
    if student_member.grades:
        try:
            grades = json.loads(student_member.grades)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Invalid grades format in server member data")

    grades[str(datetime.now())] = {
        "assignment_id": None,
        "room_id": None,
        "grade": grade_request.grade,
        "date": datetime.now().isoformat()
    }
    student_member.grades = json.dumps(grades)
    db.commit()
    db.refresh(student_member)

    return [{"user_id": grade_request.user_id, "grade": grade_request.grade}]

@app.put("/api/server/{server_id}/admin/grade", response_model=List[dict])
async def update_student_grade(server_id: int, grade_request: EditGradeRequest, db: db_dependency, Authorization: Optional[str] = Header(None)):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id,
        models.ServerMember.server_id == server.id
    ).first()
    if not server.owner_id == user.id and (not server_member or server_member.access_level <= 0):
        raise HTTPException(status_code=403, detail="User is not authorized to update grades for this student")

    student_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == grade_request.user_id,
        models.ServerMember.server_id == server_id
    ).first()
    if not student_member:
        raise HTTPException(status_code=404, detail="Student not found in the server")

    grades = {}
    if student_member.grades:
        try:
            grades = json.loads(student_member.grades)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Invalid grades format in server member data")

    if grade_request.date:
        for key, value in list(grades.items()):  # Iterate over a copy of the dictionary's items
            if value.get("date") == grade_request.date.isoformat():
                if grade_request.grade == 0:
                    grades.pop(key)  # Remove the specific grade entry with 0
                else:
                    value["grade"] = grade_request.grade
                break
        else:
            raise HTTPException(status_code=404, detail="Grade entry not found for the provided date")

    elif grade_request.assignment_id:
        collection_name = f"server_{server_id}_assignments_{grade_request.assignment_id}"
        assignment = await mongo_db[collection_name].find_one({
            "user_id": grade_request.user_id,
            "grade": {"$exists": True}
        })
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        await mongo_db[collection_name].update_one(
            {"_id": ObjectId(grade_request.assignment_id)},
            {"$set": {"grade": grade_request.grade}}
        )

    else:
        raise HTTPException(status_code=400, detail="Either assignment_id or date must be provided to update the grade")

    student_member.grades = json.dumps(grades)
    db.commit()
    db.refresh(student_member)

    return [{"user_id": grade_request.user_id, "grade": grade_request.grade}]

@app.get("/api/server/{server_id}/user/{user_id}/grades")
async def get_student_grades(server_id: int, user_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id, 
        models.ServerMember.server_id == server.id
    ).first()
    if not server.owner_id == user.id and (not server_member or server_member.access_level <= 0):
        raise HTTPException(status_code=403, detail="User is not authorized to view grades for this student")

    grades = []
    for collection_name in mongo_db.list_collection_names():
        if collection_name.startswith(f"server_{server_id}_assignments_"):
            assignments = await mongo_db[collection_name].find({"user_id": user_id}).to_list(length=None)
            for assignment in assignments:
                grades.append({
                    "assignment_id": str(assignment["_id"]),
                    "room_id": int(collection_name.split("_")[-1]),
                    "grade": assignment.get("grade", None),
                    "date": None,
                })

    student_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user_id,
        models.ServerMember.server_id == server_id
    ).first()

    if student_member and student_member.grades:
        try:
            grades_json = json.loads(student_member.grades)
            for _, grade in grades_json.items():
                grades.append({
                    "assignment_id": None,
                    "room_id": None,
                    "grade": grade['grade'],
                    "date": grade.get('date', None),
                })
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Invalid grades format in server member data")

    return grades

@app.get("/api/server/{server_id}/grades")
async def get_all_grades(server_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id, 
        models.ServerMember.server_id == server.id
    ).first()
    if not server.owner_id == user.id and (not server_member or server_member.access_level <= 0):
        raise HTTPException(status_code=403, detail="User is not authorized to view grades")

    grouped_grades: Dict[int, str, List[dict]] = {}
    members = db.query(models.ServerMember)\
        .filter(
            models.ServerMember.server_id == server_id,
            models.ServerMember.access_level == 0,  # Only students
            models.ServerMember.user_id != server.owner_id  # Exclude server owner
            ).all()
    for member in members:
        grouped_grades[member.user_id] = {"name": None, "grades": []}
        user = db.query(models.User).filter(models.User.id == member.user_id).first()
        grouped_grades[member.user_id]["name"] = user.name
        if member.grades:
            try:
                grades_json = json.loads(member.grades)
                for _, grade in grades_json.items():
                    grouped_grades[member.user_id]["grades"].append({
                        "assignment_id": None,
                        "room_id": None,
                        "grade": grade.get("grade"),
                        "date": grade.get("date")
                    })
            except json.JSONDecodeError:
                continue

    for collection_name in await mongo_db.list_collection_names():
        if collection_name.startswith(f"server_{server_id}_assignments_"):
            room_id = int(collection_name.split("_")[-1])
            assignments = await mongo_db[collection_name].find({}).sort("grade", -1).to_list(length=None)
            seen_users = set()
            for assignment in assignments:
                uid = assignment.get("user_id")
                # check to make sure user is not the server owner or has access_level > 0
                if uid is None or uid == server.owner_id:
                    continue
                server_member = db.query(models.ServerMember).filter(
                    models.ServerMember.user_id == uid, 
                    models.ServerMember.server_id == server_id
                ).first()
                if not server_member or server_member.access_level > 0:
                    continue
                if uid not in seen_users:
                    seen_users.add(uid)
                    if uid not in grouped_grades:
                        grouped_grades[uid] = {"name": None, "grades": []}
                        user = db.query(models.User).filter(models.User.id == uid).first()
                        grouped_grades[uid]["name"] = user.name if user else None
                    grouped_grades[uid]["grades"].append({
                        "assignment_id": str(assignment.get("_id")),
                        "room_id": room_id,
                        "grade": assignment.get("grade"),
                        "date": None
                    })

    return [{"user_id": uid, "name": data["name"], "grades": data["grades"]} for uid, data in grouped_grades.items()]

class BulkEditGrade(BaseModel):
    user_id: int
    grade: float
    date: Optional[datetime] = None
    assignment_id: Optional[str] = None
    room_id: Optional[int] = None

class BulkGradeUpdateRequest(BaseModel):
    updates: List[BulkEditGrade]

@app.put("/api/server/{server_id}/grades/bulk_edit")
async def bulk_edit_grades(
    server_id: int,
    request: BulkGradeUpdateRequest,
    db: db_dependency,
    Authorization: Optional[str] = Header(None)
):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id, 
        models.ServerMember.server_id == server.id
    ).first()
    if not server.owner_id == user.id and (not server_member or server_member.access_level <= 0):
        raise HTTPException(status_code=403, detail="User is not authorized to update grades")

    results = []

    for update in request.updates:
        student_member = db.query(models.ServerMember).filter_by(
            user_id=update.user_id,
            server_id=server_id
        ).first()

        if not student_member:
            continue

        if update.assignment_id and update.room_id is not None:
            # MongoDB update
            collection_name = f"server_{server_id}_assignments_{update.room_id}"
            await mongo_db[collection_name].update_one(
                {
                    "user_id": update.user_id,
                    "_id": ObjectId(update.assignment_id)
                },
                {"$set": {"grade": update.grade}},
                upsert=True
            )
        elif update.date:
            # SQL update (JSON field)
            grades = {}
            if student_member.grades:
                try:
                    grades = json.loads(student_member.grades)
                except json.JSONDecodeError:
                    continue

            updated = False
            for key, entry in grades.items():
                if entry.get("date") == update.date.isoformat():
                    entry["grade"] = update.grade
                    updated = True
                    break

            if updated:
                student_member.grades = json.dumps(grades)
                db.add(student_member)
        else:
            continue  # Skip invalid updates

        results.append({
            "user_id": update.user_id,
            "grade": update.grade,
            "assignment_id": update.assignment_id,
            "date": update.date
        })

    db.commit()
    return results

@app.get("/api/server/{server_id}/overview")
async def get_server_overview(server_id: int, db: db_dependency, Authorization: Optional[str] = Header(None)):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id,
        models.ServerMember.server_id == server.id
    ).first()
    if not server_member and server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    # Fetch all grades of current user (including from mongoDB)
    grades = {}
    if server_member and server_member.grades:
        try:
            grades = json.loads(server_member.grades)
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail="Invalid grades format in server member data")
    # add MongoDB grades
    for collection_name in await mongo_db.list_collection_names():
        if collection_name.startswith(f"server_{server_id}_assignments_"):
            assignments = await mongo_db[collection_name].find({"user_id": user.id}).to_list(length=None)
            for assignment in assignments:
                if "grade" in assignment:
                    grades[str(assignment["_id"])] = {
                        "assignment_id": str(assignment["_id"]),
                        "room_id": int(collection_name.split("_")[-1]),
                        "grade": assignment["grade"],
                        "date": assignment.get("date", None)
                    }

    # Filter out grades with grade 0 or no grade
    grades = {k: v for k, v in grades.items() if v.get("grade") not in [0, None]}
    # If no grades found, return empty list
    if not grades:
        grades = []

    # Fetch attendance
    attendance = db.query(models.Attendance).filter_by(server_id=server_id, user_id=user.id).all()
    attendance_summary = {}
    for record in attendance:
        week_number = record.week.week_number if record.week else 0
        if week_number not in attendance_summary:
            attendance_summary[week_number] = {"present": 0, "absent": 0, "excused": 0}
        attendance_summary[week_number][record.status] += 1
    
    # Assignments summary
    assignments_summary = {}
    # Get all assignment rooms for this server from PostgreSQL
    assignment_rooms = db.query(models.ServerRoom).filter(
        models.ServerRoom.server_id == server_id,
        models.ServerRoom.type.like("assignments%")
    ).all()

    for room in assignment_rooms:
      # Extract due date from room type string: "assignment YYYY-MM-DD HH:MM"
      try:
        due_date_str = room.type.split(" ", 1)[1]
        due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M")
      except Exception:
        due_date = None

      # Only include assignments with due_date in the future (or no due_date)
      if due_date is not None and due_date < datetime.now():
        continue

      collection_name = f"server_{server_id}_assignments_{room.id}"
      assignments = await mongo_db[collection_name].find({"user_id": user.id}).to_list(length=None)
      # If user has assignments, add them as before
      if assignments:
        for assignment in assignments:
          if "grade" in assignment:
            if room.id not in assignments_summary:
              assignments_summary[room.id] = []
            assignments_summary[room.id].append({
              "assignment_id": str(assignment["_id"]),
              "grade": assignment["grade"],
              "date": assignment.get("date", None),
              "due_date": due_date
            })
      else:
        # User did not submit any assignment for this room, add an empty entry
        if room.id not in assignments_summary:
          assignments_summary[room.id] = []
        assignments_summary[room.id].append({
          "assignment_id": None,
          "grade": None,
          "date": None,
          "due_date": due_date
        })

        
    # Prepare the overview response
    overview = {
        "server_name": server.name,
        "grades": grades,
        "attendance_summary": attendance_summary,
        "assignments_summary": assignments_summary,
    }

    return overview


@app.get("/api/server/{server_id}/user/{user_id}/access_level")
async def get_user_access_level(
    server_id: int,
    user_id: int,
    db: db_dependency,
    Authorization: Optional[str] = Header(None)
):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user_id, 
        models.ServerMember.server_id == server.id
    ).first()

    if not server_member and server.owner_id != user_id:
        raise HTTPException(status_code=404, detail="User not found in the server")
    

    target_member = db.query(models.ServerMember).filter_by(
        user_id=user_id,
        server_id=server_id
    ).first()

    if server.owner_id == user_id:
        return {"user_id": user_id, "access_level": 3}

    if not target_member:
        raise HTTPException(status_code=404, detail="User not found in the server")

    return {"user_id": user_id, "access_level": target_member.access_level}



# /server/${serverId}/user/${this.clickedUser.id}/access_level

class UpdateAccessLevelRequest(BaseModel):
    access_level: int

@app.patch("/api/server/{server_id}/user/{user_id}/access_level")
async def update_user_access_level(
    server_id: int,
    user_id: int,
    request: UpdateAccessLevelRequest,
    db: db_dependency,
    Authorization: Optional[str] = Header(None)
):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if server.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to update access level")

    target_member = db.query(models.ServerMember).filter_by(
        user_id=user_id,
        server_id=server_id
    ).first()

    if not target_member:
        raise HTTPException(status_code=404, detail="User not found in the server")

    target_member.access_level = request.access_level  # Extract the integer value
    db.commit()
    
    return {"user_id": user_id, "access_level": request.access_level}
    

@app.delete("/api/server/{server_id}/user/{user_id}")
async def delete_user_from_server(
    server_id: int,
    user_id: int,
    db: db_dependency,
    Authorization: Optional[str] = Header(None)
):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    # check if current user is at least access_level 1
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id,
        models.ServerMember.server_id == server_id
    ).first()

    if not (server.owner_id == user.id or (server_member and server_member.access_level > 0)):
        raise HTTPException(status_code=403, detail="Not authorized to remove users from the server")

    target_member = db.query(models.ServerMember).filter_by(
        user_id=user_id,
        server_id=server_id
    ).first()

    if not target_member:
        raise HTTPException(status_code=404, detail="User not found in the server")

    db.delete(target_member)
    db.commit()

    return {"message": f"User {user_id} removed from server {server_id}"}



class ServerOverview(BaseModel):
    server_id: int
    server_name: str
    access_level: int
    grades: List[Dict]
    attendance_summary: Dict[int, Dict[str, int]]
    assignments_summary: Dict[int, List[Dict]]
    professor_stats: Optional[Dict] = None

@app.get("/api/user/overview/")
async def get_user_servers_overview(
    db: db_dependency,
    Authorization: Optional[str] = Header(None)
):
    logger.info(f"Received GET request for /api/user/overview, Authorization: {Authorization}")
    
    if not Authorization or not Authorization.startswith("Bearer "):
        logger.error("Missing or invalid Authorization header")
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        logger.error(f"Invalid or expired token for user_id={user.id if user else 'unknown'}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Fetch all servers the user is a member of or owns
    server_members = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id
    ).all()
    owned_servers = db.query(models.Server).filter(
        models.Server.owner_id == user.id
    ).all()

    # Combine and deduplicate servers
    server_ids = set([sm.server_id for sm in server_members] + [s.id for s in owned_servers])
    servers = db.query(models.Server).filter(models.Server.id.in_(server_ids)).all()
    logger.info(f"Found {len(servers)} servers for user_id={user.id}: {server_ids}")
    
    overview = []
    try:
        tz = ZoneInfo("Europe/Bucharest")
    except Exception:
        tz = None
    now = datetime.now(tz) if tz else datetime.now()  # EEST timezone fallback
    for server in servers:
        logger.info(f"Processing server_id={server.id}, server_name={server.name}")
        
        # Get user's access level
        server_member = db.query(models.ServerMember).filter(
            models.ServerMember.user_id == user.id,
            models.ServerMember.server_id == server.id
        ).first()
        access_level = server_member.access_level if server_member else (3 if server.owner_id == user.id else 0)
        logger.info(f"User access_level={access_level} for server_id={server.id}")

        # Fetch grades
        grades = {}
        if server_member and server_member.grades:
            try:
                grades = json.loads(server_member.grades)
                logger.info(f"Loaded grades from ServerMember for server_id={server.id}: {len(grades)} entries")
            except json.JSONDecodeError as e:
                logger.error(f"Invalid grades format for user_id={user.id}, server_id={server.id}: {e}")
                grades = {}

        # Add MongoDB grades
        for collection_name in await mongo_db.list_collection_names():
            if collection_name.startswith(f"server_{server.id}_assignments_"):
                assignments = await mongo_db[collection_name].find({"user_id": user.id}).to_list(length=None)
                logger.info(f"Found {len(assignments)} assignments in {collection_name}")
                messages_collection = f"server_{server.id}_messages_{collection_name.split('_')[-1]}"
                for assignment in assignments:
                    if "grade" in assignment:
                      # Only add if user didn't send any messages in the collection
                      message_count = 0
                      if messages_collection in await mongo_db.list_collection_names():
                        message_count = await mongo_db[messages_collection].count_documents({"user_id": user.id, "assignment_id": str(assignment["_id"])})
                      if message_count == 0:
                        grades[str(assignment["_id"])] = {
                            "assignment_id": str(assignment["_id"]),
                            "room_id": int(collection_name.split("_")[-1]),
                            "grade": assignment["grade"],
                            "date": assignment.get("date", None)
                        }
                        logger.info(f"Added grade for assignment_id={assignment['_id']} in server_id={server.id}")

        # Filter out grades with grade 0 or None
        grades = [v for k, v in grades.items() if v.get("grade") not in [0, None]]
        logger.info(f"Filtered grades for server_id={server.id}: {len(grades)} valid grades")

        # Fetch attendance
        attendance = db.query(models.Attendance).filter_by(server_id=server.id, user_id=user.id).all()
        attendance_summary = {}
        for record in attendance:
            week_number = record.week.week_number if record.week else 0
            if week_number not in attendance_summary:
                attendance_summary[week_number] = {"present": 0, "absent": 0, "excused": 0}
            attendance_summary[week_number][record.status] += 1
        logger.info(f"Attendance summary for server_id={server.id}: {len(attendance_summary)} weeks")

        # Assignments summary
        assignments_summary = {}
        assignment_rooms = db.query(models.ServerRoom).filter(
            models.ServerRoom.server_id == server.id,
            models.ServerRoom.type.like("assignments%")
        ).all()
        logger.info(f"Found {len(assignment_rooms)} assignment rooms for server_id={server.id}")

        for room in assignment_rooms:
            try:
                due_date_str = room.type.split(" ", 1)[1]
                try:
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
                    due_date_iso = due_date.isoformat()
                except Exception as e:
                    logger.error(f"Failed to set timezone for due_date in room_id={room.id}, server_id={server.id}: {e}")
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d %H:%M")
                    due_date_iso = due_date.isoformat()
            except Exception as e:
                logger.error(f"Invalid due_date format for room_id={room.id}, server_id={server.id}: {e}")
                due_date = None
                due_date_iso = None

            collection_name = f"server_{server.id}_assignments_{room.id}"
            assignments = await mongo_db[collection_name].find({"user_id": user.id}).to_list(length=None)
            logger.info(f"Found {len(assignments)} assignments in {collection_name}")
            # If the user has not sent any assignments in this room, add the collection_name to assignments_summary with an empty list
            if not assignments:
              assignments_summary[room.id] = []
              assignments_summary[room.id].append({
                "assignment_name": str(room.name),
                "server_id": server.id,
                "assignment_id": room.id,
                "grade": assignment["grade"],
                "date": assignment.get("date", None),
                "due_date": due_date_iso
              })
            else:
              for assignment in assignments:
                assignments_summary[room.id] = []


        logger.info(f"Assignments summary for server_id={server.id}: {len(assignments_summary)} rooms with assignments")

        # Professor stats (for access_level > 0)
        professor_stats = None
        if access_level > 0:
            member_count = db.query(models.ServerMember).filter(
                models.ServerMember.server_id == server.id
            ).count()
            ungraded_assignments = 0
            for collection_name in await mongo_db.list_collection_names():
                if collection_name.startswith(f"server_{server.id}_assignments_"):
                    # Get all ungraded assignments
                    ungraded = await mongo_db[collection_name].find({
                        "$or": [{"grade": None}, {"grade": {"$exists": False}}]
                    }).to_list(length=None)
                    # Exclude professor messages (access_level > 0 or owner)
                    filtered_ungraded = []
                    for assignment in ungraded:
                        uid = assignment.get("user_id")
                        # Check if user is owner or has access_level > 0
                        if uid == server.owner_id:
                            continue
                        member = db.query(models.ServerMember).filter(
                            models.ServerMember.user_id == uid,
                            models.ServerMember.server_id == server.id
                        ).first()
                        if member and member.access_level > 0:
                            continue
                        filtered_ungraded.append(assignment)
                    ungraded_assignments += len(filtered_ungraded)
            professor_stats = {
                "member_count": member_count,
                "ungraded_assignments": ungraded_assignments
            }
            logger.info(f"Professor stats for server_id={server.id}: {professor_stats}")

        overview.append({
            "server_id": server.id,
            "server_name": server.name,
            "access_level": access_level,
            "grades": grades,
            "attendance_summary": attendance_summary,
            "assignments_summary": assignments_summary,
            "professor_stats": professor_stats
        })

    logger.info(f"Returning overview for user_id={user.id} with {len(overview)} servers")
    return overview


# delete message endpoint
@app.delete("/api/server/{server_id}/room/{room_id}/message/{message_id}")
async def delete_message(
    server_id: int,
    room_id: int,
    message_id: str,
    db: db_dependency,
    Authorization: Optional[str] = Header(None)
):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id,
        models.ServerMember.server_id == server.id
    ).first()
    # Allow: server owner, access_level > 0, or message author
    collection_name = f"server_{server_id}_room_{room_id}"
    if collection_name not in await mongo_db.list_collection_names():
        raise HTTPException(status_code=404, detail="Room not found")
    message = await mongo_db[collection_name].find_one({"_id": ObjectId(message_id)})
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    is_admin = server.owner_id == user.id or (server_member and server_member.access_level > 0)
    is_author = message.get("user_id") == user.id
    if not (is_admin or is_author):
        raise HTTPException(status_code=403, detail="User is not authorized to delete this message")
    result = await mongo_db[collection_name].delete_one({"_id": ObjectId(message_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Message not found or you are not the author")
    
    await websocket_manager.broadcast_textroom(room_id, "message_deleted")

    return {"message": "Message deleted successfully"}

# delete assignment endpoint
@app.delete("/api/server/{server_id}/assignment/{assignment_id}/message/{message_id}")
async def delete_assignment_message(
    server_id: int,
    assignment_id: str,
    message_id: str,
    db: db_dependency,
    Authorization: Optional[str] = Header(None)
):
    if not Authorization or not Authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = Authorization.replace("Bearer ", "")
    user = db.query(models.User).filter(models.User.token == token).first()
    if not user or user.token_expiry < datetime.now():
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    server = db.query(models.Server).filter(models.Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    
    collection_name = f"server_{server_id}_assignments_{assignment_id}"
    if collection_name not in await mongo_db.list_collection_names():
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    message = await mongo_db[collection_name].find_one({"_id": ObjectId(message_id)})
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    server_member = db.query(models.ServerMember).filter(
        models.ServerMember.user_id == user.id,
        models.ServerMember.server_id == server.id
    ).first()
    
    is_admin = server.owner_id == user.id or (server_member and server_member.access_level > 0)
    is_author = message.get("user_id") == user.id
    
    if not (is_admin or is_author):
        raise HTTPException(status_code=403, detail="User is not authorized to delete this message")
    
    result = await mongo_db[collection_name].delete_one({"_id": ObjectId(message_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Message not found or you are not the author")
    
    await websocket_manager.broadcast_textroom(int(assignment_id), "message_deleted")
    
    return {"message": "Message deleted successfully"}


# Testing endpoint to mock users
class TestUserRequest(BaseModel):
    email: str
    name: str


TEST_MODE = True  # Set to False in production

@app.post("/api/auth/test-user", response_model=User)
async def create_test_user(request: TestUserRequest, db: Session = Depends(get_db)):
    if not TEST_MODE:
        raise HTTPException(status_code=403, detail="Test endpoint disabled")
    
    user = db.query(models.User).filter(models.User.email == request.email).first()
    if not user:
        user = models.User(
            email=request.email,
            name=request.name,
            nickname=request.name.split()[0] if " " in request.name else request.name,
            picture="test_profile.png",
            token=generate_token(),
            refresh_token=generate_refresh_token(),
            token_expiry=datetime.now() + timedelta(days=1),
            refresh_token_expiry=datetime.now() + timedelta(days=7)
        )
        db.add(user)
    else:
        user.token = generate_token()
        user.refresh_token = generate_refresh_token()
        user.token_expiry = datetime.now() + timedelta(days=1)
        user.refresh_token_expiry = datetime.now() + timedelta(days=7)
    
    db.commit()
    db.refresh(user)
    
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "nickname": user.nickname,
        "picture": f"http://lamzaone.go.ro:8000/api/images/{user.picture}",
        "token": user.token,
        "refresh_token": user.refresh_token
    }

import uvicorn
from fastapi import Request
if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        reload=True,
        host="0.0.0.0"
    )

