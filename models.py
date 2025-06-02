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
    weeks = relationship("ServerWeek", back_populates="server")

class Attendance(Base):
    __tablename__ = "attendance"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"))
    server_id = Column(Integer, ForeignKey("server.id"))
    date = Column(DateTime)
    status = Column(String)  # e.g., "present", "absent", "excused"
    week = relationship("ServerWeek", back_populates="attendances")
    user = relationship("User")
    server = relationship("Server")

class ServerWeek(Base):
    __tablename__ = "server_week"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("server.id"))
    week_number = Column(Integer)  # Week number in the semester
    server = relationship("Server", back_populates="weeks")
    attendances = relationship("Attendance", back_populates="week")  # Add this relationship

# Server Member Model
class ServerMember(Base):
    __tablename__ = "server_member"

    user_id = Column(Integer, ForeignKey("user.id"), primary_key=True)
    server_id = Column(Integer, ForeignKey("server.id"), primary_key=True)
    access_level = Column(Integer, default=0)
    user = relationship("User", back_populates="memberships")
    server = relationship("Server", back_populates="members")
    grades = Column(String, default="")  # Store grades as JSON
    attendances = Column(String, default="")  # Store attendance as a JSON string or similar format



# Room Category Model
class RoomCategory(Base):
    __tablename__ = "room_category"

    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(Integer, ForeignKey("server.id"), nullable=False)
    position = Column(Integer)  # For ordering categories
    category_type = Column(String)  # e.g., "normal", "assignment"
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
