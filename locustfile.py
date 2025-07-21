from locust import HttpUser, task, between
import json
import random
import uuid
import logging

class APIUser(HttpUser):
    wait_time = between(1, 5)
    host = "http://localhost:8000"

    def on_start(self):
        """Initialize user with authentication and default IDs."""
        self.token = self.authenticate()
        self.user_id = None
        self.server_id = None
        self.room_id = None
        if self.token:
            # Get user ID from token validation
            response = self.client.post(
                "/api/auth/validate",
                json={"token": self.token},
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if response.status_code == 200:
                self.user_id = response.json()["id"]
                # Create a server for testing
                self.server_id = self.create_server()
                if self.server_id:
                    # Create a room in the server
                    self.room_id = self.create_room()

    def authenticate(self):
        """Authenticate a test user and return the token."""
        response = self.client.post(
            "/api/auth/test-user",
            json={"email": f"test_user_{random.randint(1, 10000)}@example.com", "name": "Test User"}
        )
        if response.status_code == 200:
            return response.json()["token"]
        logging.error(f"Authentication failed: {response.text}")
        return None

    def create_server(self):
        """Create a test server and return its ID."""
        if not self.token or not self.user_id:
            logging.error("Cannot create server: Missing token or user_id")
            return None
        response = self.client.post(
            "/api/server/create",
            json={
                "name": f"Test Server {random.randint(1, 1000)}",
                "description": "Test server description",
                "owner_id": self.user_id
            },
            headers={"Authorization": f"Bearer {self.token}"}
        )
        if response.status_code == 200:
            return response.json()["id"]
        logging.error(f"Server creation failed: {response.status_code} - {response.text}")
        return None

    def create_room(self):
        """Create a test room in the server and return its ID."""
        if not self.token or not self.server_id:
            logging.error("Cannot create room: Missing token or server_id")
            return None
        response = self.client.post(
            f"/api/server/{self.server_id}/room/create",
            params={
                "room_name": f"Test Room {random.randint(1, 1000)}",
                "room_type": "text"
            },
            headers={"Authorization": f"Bearer {self.token}"}
        )
        if response.status_code == 200:
            return response.json()["id"]
        logging.error(f"Room creation failed for server {self.server_id}: {response.status_code} - {response.text}")
        return None

    @task(3)
    def get_server_users(self):
        """Test retrieving server users."""
        if self.token and self.server_id:
            response = self.client.get(
                f"/api/server/{self.server_id}/users",
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if response.status_code != 200:
                logging.error(f"Get server users failed: {response.status_code} - {response.text}")

    @task(2)
    def create_message(self):
        """Test creating a message in a room."""
        if self.token and self.room_id:
            response = self.client.post(
                "/api/message",
                data={
                    "message": f"Test message {random.randint(1, 1000)}",
                    "user_token": self.token,
                    "room_id": 14,
                    "is_private": "false"
                },
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if response.status_code != 200:
                logging.error(f"Create message failed: {response.status_code} - {response.text}")

    @task(1)
    def get_messages(self):
        """Test retrieving messages for a room."""
        if self.token and self.room_id:
            response = self.client.post(
                "/api/messages/",
                json={"room_id": self.room_id},
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if response.status_code != 200:
                logging.error(f"Get messages failed: {response.status_code} - {response.text}")

    @task(2)
    def join_server(self):
        """Test joining server with ID 1 using its invite code."""
        if self.token and self.user_id:
            invite_code = 'E9mzTg'
            if invite_code:
                response = self.client.post(
                    "/api/server/join",
                    json={"invite_code": invite_code},
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                if response.status_code != 200:
                    logging.error(f"Join server 1 failed: {response.status_code} - {response.text}")
            else:
                logging.warning("Server 1 does not have an invite code")


    @task(1)
    def edit_message(self):
        """Test editing a message."""
        if self.token and self.room_id:
            # Create a message to edit
            response = self.client.post(
                "/api/message",
                data={
                    "message": f"Test message to edit {random.randint(1, 1000)}",
                    "user_token": self.token,
                    "room_id": self.room_id,
                    "is_private": "false"
                },
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if response.status_code == 200:
                message_id = response.json()["_id"]
                response = self.client.put(
                    "/api/message/edit",
                    data={
                        "message_id": message_id,
                        "room_id": self.room_id,
                        "message": f"Edited message {random.randint(1, 1000)}"
                    },
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                if response.status_code != 200:
                    logging.error(f"Edit message failed: {response.status_code} - {response.text}")
            else:
                logging.error(f"Create message for edit failed: {response.status_code} - {response.text}")

    @task(1)
    def create_assignment(self):
        """Test creating an assignment."""
        if self.token and self.room_id:
            response = self.client.post(
                "/api/assignment",
                data={
                    "message": f"Test assignment {random.randint(1, 1000)}",
                    "user_token": self.token,
                    "room_id": self.room_id,
                    "is_private": "false"
                },
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if response.status_code != 200:
                logging.error(f"Create assignment failed: {response.status_code} - {response.text}")

    @task(1)
    def get_assignments(self):
        """Test retrieving assignments for a room."""
        if self.token and self.room_id:
            response = self.client.post(
                "/api/assignments/",
                json={"room_id": self.room_id},
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if response.status_code != 200:
                logging.error(f"Get assignments failed: {response.status_code} - {response.text}")

    @task(1)
    def get_server_overview(self):
        """Test retrieving server overview."""
        if self.token and self.server_id:
            response = self.client.get(
                f"/api/server/{self.server_id}/overview",
                headers={"Authorization": f"Bearer {self.token}"}
            )
            if response.status_code != 200:
                logging.error(f"Get server overview failed: {response.status_code} - {response.text}")

# Run with: locust -f locustfile.py --host=http://localhost:8000