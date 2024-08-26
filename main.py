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
from google.auth.transport import requests

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
    return secrets.token_urlsafe(32)

db_dependency = Annotated[Session, Depends(get_db)]

class TokenRequest(BaseModel):
    token: str

class User(BaseModel):
    id: int
    email: str
    name: str
    nickname: str
    picture: str

class UserIn(BaseModel):
    token: str

@app.post("/auth/google", response_model=User)
def google_auth(token_request: UserIn, db: db_dependency):
    idinfo = id_token.verify_oauth2_token(token_request.token, requests.Request(), CLIENT_ID)
    
    # If idinfo is a string, parse it
    if isinstance(idinfo, str):
        google_data = json.loads(idinfo)
    else:
        google_data = idinfo

    print(google_data)
    # Now google_data should be a dictionary, and you can access its fields
    email = google_data['email']
    name = "john doe" # google_data['name']
    picture = "https://example.com/picture.jpg" # google_data['picture']

    # Check if user already exists in the database
    db_user = db.query(models.User).filter(models.User.email == email).first()
    
    if not db_user:
        # If the user doesn't exist, create a new one
        refresh_token = generate_refresh_token()
        db_user = models.User(
            email=email,
            name=name,
            nickname=name,
            picture=picture,
            token=token_request.token,
            refresh_token=refresh_token,
            token_expiry=datetime.now() + timedelta(days=1),
            refresh_token_expiry=datetime.now() + timedelta(days=7)
        )
        db.add(db_user)
    else:
        # Update existing user tokens and expiry dates
        db_user.token = token_request.token
        db_user.refresh_token = generate_refresh_token()
        db_user.token_expiry = datetime.now() + timedelta(days=1)
        db_user.refresh_token_expiry = datetime.now() + timedelta(days=7)

    db.commit()
    db.refresh(db_user)
    return db_user
