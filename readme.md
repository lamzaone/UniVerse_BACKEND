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