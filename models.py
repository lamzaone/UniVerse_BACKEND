from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, DateTime
from database import Base

from datetime import datetime, timedelta
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    nickname = Column(String)
    picture = Column(String)
    token = Column(String)
    refresh_token = Column(String)
    token_expiry = Column(DateTime)
    refresh_token_expiry = Column(DateTime)

    def refresh_tokens(self):
        self.token_expiry = datetime.now() + timedelta(days=1)
        self.refresh_token_expiry = datetime.now() + timedelta(days=7)

    def set_tokens(self, token, refresh_token):
        self.token = token
        self.token_expiry = datetime.now() + timedelta(days=1)
        self.refresh_token = refresh_token
        self.refresh_token_expiry = datetime.now() + timedelta(days=7)

class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"))
    picture = Column(String)
    created_at = Column(DateTime)

class Server(Base):
    __tablename__ = "servers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"))
    picture = Column(String)
    created_at = Column(DateTime)

class Channel(Base):
    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(String)
    server_id = Column(Integer, ForeignKey("servers.id"))
    created_at = Column(DateTime)

