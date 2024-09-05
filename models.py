from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, LargeBinary
from sqlalchemy.orm import relationship
from database import Base

# User Model
class User(Base):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    faculty = Column(String)  # VARCHAR2
    year = Column(Integer)
    nickname = Column(String)
    picture = Column(String)  # Store image path
    token = Column(String)
    refresh_token = Column(String)
    token_expiry = Column(DateTime)
    refresh_token_expiry = Column(DateTime)

    servers_owned = relationship("Server", back_populates="owner")
    memberships = relationship("ServerMember", back_populates="user")

# Faculty Model
class Faculty(Base):
    __tablename__ = "faculty"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    dean_id = Column(Integer, ForeignKey("user.id"))

# Faculty Enrollment Model
class FacultyEnrollment(Base):
    __tablename__ = "faculty_enrollment"

    user_id = Column(Integer, ForeignKey("user.id"), primary_key=True)
    faculty_id = Column(Integer, ForeignKey("faculty.id"), primary_key=True)
    year = Column(Integer)
    group = Column(Integer)

# Server Model
class Server(Base):
    __tablename__ = "server"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    description = Column(String)
    owner_id = Column(Integer, ForeignKey("user.id"))
    invite_code = Column(String)
    created_at = Column(DateTime)

    owner = relationship("User", back_populates="servers_owned")
    members = relationship("ServerMember", back_populates="server")
    categories = relationship("RoomCategory", back_populates="server")

# Server Member Model
class ServerMember(Base):
    __tablename__ = "server_member"

    user_id = Column(Integer, ForeignKey("user.id"), primary_key=True)
    server_id = Column(Integer, ForeignKey("server.id"), primary_key=True)
    access_level = Column(Integer)

    user = relationship("User", back_populates="memberships")
    server = relationship("Server", back_populates="members")

# Room Category Model
class RoomCategory(Base):
    __tablename__ = "room_category"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("server.id"), nullable=False)
    position = Column(Integer)  # For ordering categories
    name = Column(String, nullable=False)
    
    server = relationship("Server", back_populates="categories")
    rooms = relationship("ServerRoom", back_populates="category", cascade="all, delete-orphan")

# Server Room Model
class ServerRoom(Base):
    __tablename__ = "server_room"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String)
    server_id = Column(Integer, ForeignKey("server.id"))
    name = Column(String)
    category_id = Column(Integer, ForeignKey("room_category.id"), nullable=True)  # Allow NULL for rooms without a category
    position = Column(Integer)  # Position within the category or ungrouped list

    category = relationship("RoomCategory", back_populates="rooms")
