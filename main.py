import base64
from fastapi import FastAPI, HTTPException, Depends, Body
from pydantic import BaseModel
from typing import Annotated
from sqlalchemy.orm import Session
import requests
from datetime import datetime, timedelta
from database import engine, SessionLocal
import secrets
import json
import models
from fastapi.middleware.cors import CORSMiddleware
from google.oauth2 import id_token
import requests
import logging
import os
from fastapi.responses import FileResponse
from pydantic import BaseModel

models.Base.metadata.create_all(bind=engine)

app = FastAPI()
CLIENT_ID = "167769953872-b5rnqtgjtuhvl09g45oid5r9r0lui2d6.apps.googleusercontent.com"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],  # Adjust this to match your frontend's URL
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, PUT, DELETE, OPTIONS, etc.)
    allow_headers=["*"],  # Allow all headers
)

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
        orm_mode = True

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

class ServerCreate(BaseModel):
    name: str
    description: str
    owner_id: int

IMAGE_DIR = "user_images"  # Directory to store user images
os.makedirs(IMAGE_DIR, exist_ok=True)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def generate_token():
    return secrets.token_urlsafe(32)

def generate_refresh_token():
    return secrets.token_urlsafe(64)

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

@app.get("/images/{image_name}")
async def serve_image(image_name: str):
    """Endpoint to serve user images."""
    file_path = os.path.join(IMAGE_DIR, image_name)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(file_path)

@app.post("/auth/google", response_model=User)
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
        db_user.refresh_token = generate_refresh_token()
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
        "picture": f"http://localhost:8000/images/{db_user.picture}",  # Provide URL for frontend
        "token": db_user.token,
        "refresh_token": db_user.refresh_token
    }
    
    return db_user_data

@app.post("/auth/refresh", response_model=User)
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
        picture=f"http://localhost:8000/images/{db_user.picture}",
        token=db_user.token,
        refresh_token=db_user.refresh_token,
    )
    
    return user_response


@app.post("/auth/validate", response_model=User)
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
        picture=f"http://localhost:8000/images/{db_user.picture}",
        token=db_user.token,
        refresh_token=db_user.refresh_token,
    )
    
    return user_response


@app.post("/server/create", response_model=Server)
def create_server(server: ServerCreate, db: db_dependency):
    db_server = models.Server(
        name=server.name,
        description=server.description,
        owner_id=server.owner_id
    )
    db_server.invite_code=secrets.token_urlsafe(6),
    db_server.created_at=datetime.now()

    
    db.add(db_server)
    db.commit()
    db.refresh(db_server)
    
    return db_server

@app.get("/server/user/{user_id}", response_model=list[Server])
def get_servers(user_id: int, db: db_dependency):
    owned_servers = db.query(models.Server).filter(models.Server.owner_id == user_id).all()
    db_servers = db.query(models.Server).join(models.ServerMember).filter(models.ServerMember.user_id == user_id).all()
    db_servers.extend(owned_servers)
    return db_servers

class GetServer(BaseModel):
    server_id: int
    user_id: int


@app.post("/server", response_model=Server)
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


class JoinServer(BaseModel):
    invite_code: str
    user_id: int

@app.post("/server/join", response_model=Server)
def join_server(server_info: JoinServer, db: db_dependency):
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

        # Return the server information
        return db_server
    
    except Exception as e:
        # Handle SQL-related errors
        raise HTTPException(status_code=500, detail="Internal Server Error") from e
