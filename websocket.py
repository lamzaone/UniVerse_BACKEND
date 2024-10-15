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


