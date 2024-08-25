from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Annotated
import models
from database import engine, SessionLocal
from sqlalchemy.orm import Session

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]

class User(BaseModel):
    id: int
    email: str
    name: str
    nickname: str
    picture: str

class Group(BaseModel):
    id: int
    name: str
    description: str
    owner_id: int
    picture: str
    created_at: str

class Server(BaseModel):
    id: int
    name: str
    description: str
    owner_id: int
    picture: str
    created_at: str

class Channel(BaseModel):
    id: int
    name: str
    description: str
    server_id: int
    created_at: str

@app.post("/user", response_model=User)
def create_user(user: User, db: Session = Depends(get_db)):
    db_user = models.User(id=user.id, email=user.email, name=user.name, nickname=user.nickname, picture=user.picture)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@app.get("/user/{user_id}", response_model=User)
def read_user(user_id: int, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(models.User.id == user_id).first()
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user
