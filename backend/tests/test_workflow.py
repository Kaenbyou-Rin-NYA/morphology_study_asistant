from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_demo_wsi_and_learning_flow():
    slides = client.get('/api/slides').json()
    demo = next(slide for slide in slides if slide['id'] == 'aorta-demo')
    assert demo['width'] > 10000
    assert client.get('/api/slides/aorta-demo/image.dzi').status_code == 200
    tile = client.get('/api/slides/aorta-demo/image_files/0/0_0.jpeg')
    assert tile.status_code == 200 and tile.content.startswith(b'\xff\xd8')

    start = client.post('/api/sessions', json={'slide_id': demo['id'], 'goal': '认识主动脉组织区域'})
    assert start.status_code == 200
    session = start.json()
    assert session['target']['annotation']
    session_id = session['id']
    assert client.post(f'/api/sessions/{session_id}/answers', json={'x': 1, 'y': 1}).status_code == 409
    assert client.post(f'/api/sessions/{session_id}/exam').status_code == 200
    assert client.post(f'/api/sessions/{session_id}/answers', json={'x': -1, 'y': 1}).status_code == 422
    miss = client.post(f'/api/sessions/{session_id}/answers', json={'x': 1, 'y': 1}).json()
    assert miss['correct'] is False
    x1, y1, x2, y2 = session['target']['bbox']
    hit = client.post(f'/api/sessions/{session_id}/answers', json={'x': (x1+x2)/2, 'y': (y1+y2)/2}).json()
    assert hit['correct'] is True
    report = client.post(f'/api/sessions/{session_id}/review').json()
    assert (report['correct'], report['count'], report['accuracy']) == (1, 2, 50)


def test_unsupported_goal_and_upload_format():
    response = client.post('/api/sessions', json={'slide_id': 'aorta-demo', 'goal': '单层柱状上皮'})
    assert response.status_code == 422
    response = client.post('/api/slides/upload', files={'file': ('invalid.exe', b'not a slide')})
    assert response.status_code == 400
