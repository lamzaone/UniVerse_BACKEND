# WebSocket Connections Manager
from typing import Annotated, Dict, List
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from requests import Session
from database import engine, SessionLocal

import models


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

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]
app = FastAPI()

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