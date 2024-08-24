import requests

post = requests.post("http://localhost:8000/user", json={"id": 5, "name": "John Doe", "age": 25, "email": ""})
print(post.json())
print(post.status_code)
print(requests.get("http://localhost:8000/user/5").json())