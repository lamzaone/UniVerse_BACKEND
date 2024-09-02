from sqlalchemy import Boolean, Column,DateTime, ForeignKey, Integer, String, LargeBinary
from sqlalchemy.orm import relationship
from database import Base
import secrets

# User Model
class User(Base):
    __tablename__ = "user"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    name = Column(String)
    faculty = Column(String)  # VARCHAR2
    year = Column(Integer)
    nickname = Column(String)
    picture = Column(String)  # Change from LargeBinary to String to store image path
    token = Column(String)
    refresh_token = Column(String)
    token_expiry = Column(DateTime)
    refresh_token_expiry = Column(DateTime)


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


    

# Server Member Model
class ServerMember(Base):
    __tablename__ = "server_member"
    
    user_id = Column(Integer, ForeignKey("user.id"), primary_key=True)
    server_id = Column(Integer, ForeignKey("server.id"), primary_key=True)
    access_level = Column(Integer)
    

# Server Room Model
class ServerRoom(Base):
    __tablename__ = "server_room"
    
    id = Column(Integer, primary_key=True, index=True)
    type = Column(String)
    server_id = Column(Integer, ForeignKey("server.id"))
    name = Column(String)
    
