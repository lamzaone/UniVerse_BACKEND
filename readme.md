# Setting up
1. Download and install PostgreSQL
2. Clone this repository
3. Run the following commands in the repository folder
```
pip install -r requirements.txt
```
4. You can start the backend server by running the following command:
```
uvicorn main:app
```

Once running, you'll be able to access your docs by accessing http://127.0.0.1:8000/docs. There, you'll be able to test the endpoints.





unix:

sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
sudo ufw allow 8000/tcp
sudo ufw enable

sudo -i -u postgres
psql

CREATE DATABASE uniVerse;
CREATE USER your_username WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE uniVerse TO your_username;
psql

GRANT ALL PRIVILEGES ON DATABASE uniVerse TO your_username;
\c uniVerse
GRANT ALL PRIVILEGES ON SCHEMA public TO your_username;

pip install uvicorn[standard]