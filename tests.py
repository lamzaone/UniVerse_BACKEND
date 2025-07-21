import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from main import app, websocket_manager, get_db, models
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from pydantic import BaseModel
import json
from bson import ObjectId
from fastapi import WebSocket
import asyncio

# Test client for FastAPI
client = TestClient(app)

# Mock database dependency
@pytest_asyncio.fixture
async def mock_db():
    db = MagicMock(spec=Session)
    yield db

# Mock MongoDB client
@pytest_asyncio.fixture
async def mock_mongo():
    mongo = AsyncMock()
    mongo.uniVerse = AsyncMock()
    yield mongo

# Override dependency for tests
def override_get_db():
    db = MagicMock(spec=Session)
    yield db

app.dependency_overrides[get_db] = override_get_db

@pytest_asyncio.fixture
async def mock_user():
    return models.User(
        id=1,
        email="test@example.com",
        name="Test User",
        nickname="Tester",
        picture="test_profile.png",
        token="valid_token",
        refresh_token="valid_refresh_token",
        token_expiry=datetime.now() + timedelta(days=1),
        refresh_token_expiry=datetime.now() + timedelta(days=7)
    )

@pytest.mark.asyncio
async def test_google_auth_success(mock_db, mock_user):
    # Mock Google API responses
    with patch("requests.get") as mock_get:
        mock_get.side_effect = [
            MagicMock(status_code=200, json=lambda: {
                "email": "test@example.com",
                "aud": "167769953872-b5rnqtgjtuhvl09g45oid5r9r0lui2d6.apps.googleusercontent.com"
            }),
            MagicMock(status_code=200, json=lambda: {
                "name": "Test User",
                "picture": "http://example.com/test_profile.png"
            })
        ]

        # Mock database query
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        mock_db.refresh = MagicMock()

        # Mock file saving
        with patch("main.save_image_to_filesystem", return_value="user_images/test_profile.png"):
            response = client.post(
                "/api/auth/google",
                json={"id_token": "valid_id_token", "access_token": "valid_access_token"}
            )

        assert response.status_code == 200
        assert response.json()["email"] == "test@example.com"
        assert response.json()["name"] == "Test User"
        assert response.json()["picture"].endswith("test_profile.png")

@pytest.mark.asyncio
async def test_google_auth_invalid_token(mock_db):
    with patch("requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=400)
        response = client.post(
            "/api/auth/google",
            json={"id_token": "invalid_id_token", "access_token": "invalid_access_token"}
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid token"

@pytest.mark.asyncio
async def test_refresh_token_success(mock_db, mock_user):
    mock_db.query.return_value.filter.return_value.first.return_value = mock_user
    response = client.post(
        "/api/auth/refresh",
        json={"token": "valid_refresh_token"}
    )

    assert response.status_code == 200
    assert response.json()["id"] == 1
    assert response.json()["email"] == "test@example.com"

@pytest.mark.asyncio
async def test_refresh_token_expired(mock_db, mock_user):
    mock_user.refresh_token_expiry = datetime.now() - timedelta(days=1)
    mock_db.query.return_value.filter.return_value.first.return_value = mock_user
    response = client.post(
        "/api/auth/refresh",
        json={"token": "valid_refresh_token"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Refresh token expired"

@pytest.mark.asyncio
async def test_create_server(mock_db, mock_user):
    mock_db.query.return_value.filter.return_value.first.return_value = mock_user
    mock_db.add = MagicMock()
    mock_db.commit = MagicMock()
    mock_db.refresh = MagicMock()

    response = client.post(
        "/api/server/create",
        json={"name": "Test Server", "description": "A test server", "owner_id": 1},
        headers={"Authorization": "Bearer valid_token"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Test Server"
    assert response.json()["owner_id"] == 1

@pytest.mark.asyncio
async def test_join_server_success(mock_db, mock_user):
    mock_server = models.Server(
        id=1, name="Test Server", invite_code="abcd1234", owner_id=2
    )
    mock_db.query.side_effect = [
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_user)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_server)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None))))
    ]
    mock_db.add = MagicMock()
    mock_db.commit = MagicMock()

    response = client.post(
        "/api/server/join",
        json={"invite_code": "abcd1234"},
        headers={"Authorization": "Bearer valid_token"}
    )

    assert response.status_code == 200
    assert response.json()["id"] == 1
    assert response.json()["name"] == "Test Server"

@pytest.mark.asyncio
async def test_join_server_already_member(mock_db, mock_user):
    mock_server = models.Server(
        id=1, name="Test Server", invite_code="abcd1234", owner_id=2
    )
    mock_member = models.ServerMember(user_id=1, server_id=1)
    mock_db.query.side_effect = [
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_user)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_server)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_member))))
    ]

    response = client.post(
        "/api/server/join",
        json={"invite_code": "abcd1234"},
        headers={"Authorization": "Bearer valid_token"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "User is already a member of the server"

@pytest.mark.asyncio
async def test_websocket_main_connection():
    with patch("main.websocket_manager") as mock_manager:
        mock_manager.connect_main = AsyncMock()
        mock_manager.disconnect_main = MagicMock()
        mock_manager.broadcast_main = AsyncMock()

        async with client.websocket_connect("/api/ws/main/1") as websocket:
            await websocket.send_text("Hello")
            await websocket.close()

        mock_manager.connect_main.assert_awaited()
        mock_manager.disconnect_main.assert_called()
        mock_manager.broadcast_main.assert_awaited_with("Main Server Update for User 1: Hello")

@pytest.mark.asyncio
async def test_store_message(mock_db, mock_mongo, mock_user):
    mock_server = models.Server(id=1, name="Test Server", owner_id=2)
    mock_room = models.ServerRoom(id=1, server_id=1, type="text")
    mock_db.query.side_effect = [
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_user)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_room)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_server))))
    ]

    mock_mongo.uniVerse.__getitem__.return_value.insert_one = AsyncMock(return_value=MagicMock(inserted_id=ObjectId("507f1f77bcf86cd799439011")))
    
    with patch("main.websocket_manager.broadcast_textroom", new=AsyncMock()):
        response = client.post(
            "/api/message",
            data={
                "message": "Test message",
                "user_token": "valid_token",
                "room_id": 1,
                "is_private": False
            },
            headers={"Authorization": "Bearer valid_token"}
        )

    assert response.status_code == 200
    assert response.json()["message"] == "Test message"
    assert response.json()["room_id"] == 1
    assert response.json()["_id"] == "507f1f77bcf86cd799439011"

@pytest.mark.asyncio
async def test_get_messages(mock_db, mock_mongo, mock_user):
    mock_server = models.Server(id=1, name="Test Server", owner_id=2)
    mock_room = models.ServerRoom(id=1, server_id=1, type="text")
    mock_db.query.side_effect = [
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_user)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_room)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=mock_server)))),
        MagicMock(filter=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None))))
    ]

    mock_messages = [
        {
            "_id": ObjectId("507f1f77bcf86cd799439011"),
            "message": "Test message",
            "user_id": 1,
            "room_id": 1,
            "is_private": False,
            "timestamp": datetime.now().isoformat(),
            "attachments": []
        }
    ]
    mock_mongo.uniVerse.__getitem__.return_value.find.return_value.sort.return_value.limit.return_value.to_list = AsyncMock(return_value=mock_messages)

    response = client.post(
        "/api/messages/",
        json={"room_id": 1},
        headers={"Authorization": "Bearer valid_token"}
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["message"] == "Test message"
    assert response.json()[0]["_id"] == "507f1f77bcf86cd799439011"

# Performance tests using Locust
<xaiArtifact artifact_id="d8c3a458-1c25-4df1-96bc-fc32c85cdf70" artifact_version_id="b06d116f-4e08-4ba6-86e9-d74e9260c9ab" title="locustfile.py" contentType="text/python">
from locust import HttpUser, task, between, WebSocketUser
from locust.clients import WebSocketClient
import json
import random

class APIUser(HttpUser):
    wait_time = between(1, 5)

    def on_start(self):
        # Authenticate and get token
        self.token = self.authenticate()
        self.user_id = 1
        self.server_id = 1
        self.room_id = 1

    def authenticate(self):
        response = self.client.post(
            "/api/auth/google",
            json={"id_token": "test_id_token", "access_token": "test_access_token"}
        )
        if response.status_code == 200:
            return response.json()["token"]
        return None

    @task(3)
    def get_server_users(self):
        if self.token:
            self.client.get(
                f"/api/server/{self.server_id}/users",
                headers={"Authorization": f"Bearer {self.token}"}
            )

    @task(2)
    def create_message(self):
        if self.token:
            self.client.post(
                "/api/message",
                data={
                    "message": f"Test message {random.randint(1, 1000)}",
                    "user_token": self.token,
                    "room_id": self.room_id,
                    "is_private": False
                },
                headers={"Authorization": f"Bearer {self.token}"}
            )

    @task(1)
    def get_messages(self):
        if self.token:
            self.client.post(
                "/api/messages/",
                json={"room_id": self.room_id},
                headers={"Authorization": f"Bearer {self.token}"}
            )

class WebSocketUser(WebSocketUser):
    wait_time = between(1, 5)

    def on_start(self):
        self.user_id = 1
        self.room_id = 1
        self.server_id = 1
        self.websocket = WebSocketClient(self.host + f"/api/ws/textroom/{self.room_id}/{self.user_id}")
        self.websocket.connect()

    @task
    def send_message(self):
        message = json.dumps({"message": f"Test message {random.randint(1, 1000)}"})
        self.websocket.send(message)

    def on_stop(self):
        if hasattr(self, 'websocket'):
            self.websocket.close()

# Run with: locust -f locustfile.py --host=http://localhost:8000