# Setting up

## Windows setup

1. Download and install PostgreSQL
2. Download and instal MongoDB Community Edition
3. Clone this repository
4. Run the following commands in the repository folder

```bash
pip install -r requirements.txt
```

---

## Ubuntu setup

1.1 Install PostgreSQL:

   1. `sudo apt-get update`
   2. `sudo apt-get upgrade`
   3. `sudo apt install postgresql`
   4. `sudo apt install -y python3-dev libpq-dev gcc`
   <!-- 5. sudo apt install ufw
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw allow 22/tcp
   sudo ufw allow 8000/tcp
   sudo ufw enable -->

1.2 Install MongoDB Community Edition:

   1. `sudo apt-get install gnupg curl`
   2. Import MongoDB public key

```bash
curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | \
   sudo gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg \
   --dearmor
```

   3. `echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/8.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list`
   4. `sudo apt-get update`
   5. `sudo apt-get install -y mongodb-org`
   6. `sudo systemctl daemon-reload`
   7. `sudo systemctl start mongod`
   

2. Run these commands in terminal to setup PostgresSQL
 
```bash
sudo service postgresql start
```

```bash
sudo -i -u postgres
psql
```

```bash
ALTER USER postgres WITH PASSWORD 'pass';
CREATE DATABASE universe;
GRANT ALL PRIVILEGES ON DATABASE universe TO postgres;
ALTER ROLE postgres CREATEDB;
ALTER ROLE postgres SUPERUSER;
```

```bash
\c universe
GRANT ALL PRIVILEGES ON SCHEMA public TO postgres;
```

1. Install python3 and install all requirements in a virtual environment

```bash
sudo chmod +777 /path/to/repo/
sudo chmod +777 /path/to/repo/*
cd /path/to/repo/
sudo apt install python3.12-venv
sudo apt install python3
sudo python3 -m venv venv

source venv/bin/activate
pip install -r requirements.txt
```

---
## Starting the backend

You can start the backend server by running the following command:

```bash
python3 main.py
```
