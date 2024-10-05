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





# Unix setup for VM:

1. sudo apt-get update
2. sudo apt-get upgrade
3. sudo apt install postgresql
4. sudo apt-get install libpq-dev
5. sudo apt install ufw
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
sudo ufw allow 8000/tcp
sudo ufw enable



sudo service postgresql start
sudo -i -u postgres

CREATE USER ubuntu WITH LOGIN PASSWORD 'pass';
CREATE DATABASE universe;
GRANT ALL PRIVILEGES ON DATABASE universe TO ubuntu;
ALTER ROLE ubuntu CREATEDB;
ALTER ROLE ubuntu SUPERUSER;

\c universe
GRANT ALL PRIVILEGES ON SCHEMA public TO ubuntu;
replace uniVerse with universe in database.py postgresql url

sudo chmod +777 /path/to/repo/
sudo chmod +777 /path/to/repo/*
cd /path/to/repo/
sudo apt install python3.12-venv
sudo apt install python3
sudo python3 -m venv venv

source venv/bin/activate
pip install -r requirements.txt

uvicorn main:app to launch

