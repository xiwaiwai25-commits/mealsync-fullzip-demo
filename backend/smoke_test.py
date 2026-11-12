from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
assert client.get('/api/health').status_code == 200
student = client.post('/api/auth/login', json={'email':'student@mealsync.test','password':'student123'})
assert student.status_code == 200
headers = {'Authorization': 'Bearer ' + student.json()['token']}
assert len(client.get('/api/shops', headers=headers).json()) == 8
assert client.get('/api/home', headers=headers).status_code == 200
assert client.get('/api/orders', headers=headers).status_code == 200
vendor = client.post('/api/auth/login', json={'email':'vendor@mealsync.test','password':'vendor123'})
assert vendor.status_code == 200
vh = {'Authorization': 'Bearer ' + vendor.json()['token']}
assert client.get('/api/vendor/summary', headers=vh).status_code == 200
print('MealSync API smoke tests passed.')
