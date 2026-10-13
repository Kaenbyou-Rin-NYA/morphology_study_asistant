from fastapi.testclient import TestClient
import numpy as np

from app.main import app
from app import main, storage

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
    assert hit['x'] == (x1+x2)/2 and hit['result'] == 'correct'
    assert [a['result'] for a in client.get(f'/api/sessions/{session_id}/answers').json()] == ['incorrect', 'correct']
    report = client.post(f'/api/sessions/{session_id}/review').json()
    assert (report['correct'], report['count'], report['accuracy']) == (1, 2, 50)


def test_unsupported_goal_and_upload_format():
    response = client.post('/api/sessions', json={'slide_id': 'aorta-demo', 'goal': '单层柱状上皮'})
    assert response.status_code == 422
    response = client.post('/api/slides/upload', files={'file': ('invalid.exe', b'not a slide')})
    assert response.status_code == 400


def test_other_location_same_class_and_uncertain(monkeypatch, tmp_path):
    """Separate instances of the same class must be accepted outside tour bbox."""
    item = main.slides['aorta-demo']
    task = {'id': 'test-other-location', 'slide_id': item['id'], 'class_id': 'nuclei',
            'status': 'completed', 'progress': 100, 'candidates': [
                {'id': 'tour', 'class_id': 'nuclei', 'bbox': [100, 100, 200, 200], 'similarity': 1,
                 'status': 'predicted', 'polygon': None, 'validated': False, 'mask_ref': 'tour.npy'},
                {'id': 'another', 'class_id': 'nuclei', 'bbox': [1000, 1000, 1100, 1100], 'similarity': .9,
                 'status': 'predicted', 'polygon': None, 'validated': False, 'mask_ref': 'another.npy'}],
            'models': {'segmentor': 'test'}}
    storage.save('analysis', task)
    monkeypatch.setattr(main.analysis, 'MASKS', tmp_path)
    for name in ('tour', 'another'):
        mask = np.zeros((10, 10), dtype=np.int32)
        mask[4:7, 4:7] = 1
        np.save(tmp_path / f'{name}.npy', mask)
    monkeypatch.setattr(main.ai, 'model_status', lambda: {'instanseg': {'configured': False}, 'cellpose': {'configured': False},
        'conch': {'configured': False}, 'medsam': {'configured': False}, 'uni': {'configured': False, 'classifier_configured': False}})
    started = client.post('/api/sessions', json={'slide_id': item['id'], 'goal': '寻找细胞核', 'analysis_id': task['id']}).json()
    identity = started['id']
    client.post(f'/api/sessions/{identity}/exam')
    other = client.post(f'/api/sessions/{identity}/answers', json={'x': 1050, 'y': 1050}).json()
    assert other['result'] == 'correct' and other['instance_id'] == 'another:1'
    uncertain = client.post(f'/api/sessions/{identity}/answers', json={'x': 4000, 'y': 4000}).json()
    assert uncertain['result'] == 'uncertain' and uncertain['correct'] is None
    review = client.post(f'/api/sessions/{identity}/review').json()
    assert (review['correct'], review['scored'], review['uncertain']) == (1, 1, 1)


def test_configuration_and_upload_limit():
    assert client.get('/api/config').json()['max_upload_bytes'] > 1024**3
    response = client.post('/api/slides/aorta-demo/analyses', json={'goal': '寻找纹状缘'})
    assert response.status_code == 409 and 'CONCH_CHECKPOINT' in response.json()['detail']


def test_upload_limit_cleans_partial_file(monkeypatch):
    monkeypatch.setattr(main, 'MAX_UPLOAD', 4)
    before = set(main.UPLOADS.iterdir())
    response = client.post('/api/slides/upload', files={'file': ('small.svs', b'123456')})
    assert response.status_code == 413
    assert set(main.UPLOADS.iterdir()) == before


def test_nuclei_pipeline_builds_local_masks(monkeypatch, tmp_path):
    from app import analysis
    from app.catalog import resolve
    monkeypatch.setattr(analysis, 'MASKS', tmp_path)
    monkeypatch.setattr(analysis, 'positions', lambda slide, target, point: ([(int(slide.dimensions[0] * .79), int(slide.dimensions[1] * .34), 256)], .25))
    monkeypatch.setattr(analysis.ai, 'model_status', lambda: {
        'instanseg': {'configured': True}, 'cellpose': {'configured': False},
        'conch': {'configured': False}, 'medsam': {'configured': False},
        'uni': {'configured': False, 'classifier_configured': False}})
    monkeypatch.setattr(analysis.ai, 'segment', lambda image, pixel_size, kind: np.ones((16, 16), dtype=np.int32))
    task = {'id': 'test-local-pipeline', 'slide_id': 'aorta-demo', 'class_id': 'nuclei', 'status': 'queued',
            'progress': 0, 'candidates': [], 'point': [100, 100], 'version': 1}
    analysis.run(task, main.slides['aorta-demo'], resolve('细胞核'))
    assert task['status'] == 'completed' and task['candidates']
    item = task['candidates'][0]
    assert item['mask_ref'] and item['status'] == 'predicted'
    x1, y1, x2, y2 = item['bbox']
    assert analysis.mask_hit(item, (x1+x2)/2, (y1+y2)/2)


def test_pending_answer_reconciles_after_analysis(monkeypatch):
    from app.catalog import resolve
    item = main.slides['aorta-demo']
    task = {'id': 'pending-session-analysis', 'slide_id': item['id'], 'class_id': 'nuclei',
            'status': 'completed', 'progress': 100, 'candidates': [
                {'id': 'nucleus', 'class_id': 'nuclei', 'bbox': [100, 100, 200, 200], 'similarity': 1,
                 'status': 'predicted', 'polygon': None, 'validated': False, 'mask_ref': 'virtual.npy'}],
            'models': {'segmentor': 'test'}}
    storage.save('analysis', task)
    monkeypatch.setattr(main.analysis, 'mask_hit', lambda candidate, x, y: 1 if 100 <= x < 200 and 100 <= y < 200 else 0)
    monkeypatch.setattr(main.ai, 'model_status', lambda: {'instanseg': {'configured': True}, 'cellpose': {'configured': False},
        'conch': {'configured': False}, 'medsam': {'configured': False}, 'uni': {'configured': False, 'classifier_configured': False}})
    monkeypatch.setattr(main.analysis, 'create', lambda slide, target, point=None: {'id': 'local-pending-id'})
    started = client.post('/api/sessions', json={'slide_id': item['id'], 'goal': resolve('细胞核')['name'], 'analysis_id': task['id']}).json()
    client.post(f"/api/sessions/{started['id']}/exam")
    pending = client.post(f"/api/sessions/{started['id']}/answers", json={'x': 300, 'y': 300}).json()
    assert pending['result'] == 'pending'
    storage.save('analysis', {'id': 'local-pending-id', 'slide_id': item['id'], 'class_id': 'nuclei', 'status': 'completed', 'candidates':[]})
    history = client.get(f"/api/sessions/{started['id']}/answers").json()
    assert history[0]['result'] == 'uncertain' and history[0]['correct'] is None


def test_chunked_upload_resume_and_complete(monkeypatch, tmp_path):
    monkeypatch.setattr(main, 'UPLOADS', tmp_path)
    monkeypatch.setattr(main.executor, 'submit', lambda *args: None)
    opened = client.post('/api/uploads', json={'filename': 'example.svs', 'size': 6})
    assert opened.status_code == 201
    uid = opened.json()['id']
    first = client.put(f'/api/uploads/{uid}/chunk', content=b'abc', headers={'Content-Range': 'bytes 0-2/6'})
    assert first.status_code == 200 and first.json()['offset'] == 3
    assert client.get(f'/api/uploads/{uid}').json()['offset'] == 3
    wrong = client.put(f'/api/uploads/{uid}/chunk', content=b'abc', headers={'Content-Range': 'bytes 0-2/6'})
    assert wrong.status_code == 409
    short = client.put(f'/api/uploads/{uid}/chunk', content=b'd', headers={'Content-Range': 'bytes 3-5/6'})
    assert short.status_code == 400 and client.get(f'/api/uploads/{uid}').json()['offset'] == 3
    last = client.put(f'/api/uploads/{uid}/chunk', content=b'def', headers={'Content-Range': 'bytes 3-5/6'})
    assert last.status_code == 200 and last.json()['offset'] == 6
    done = client.post(f'/api/uploads/{uid}/complete')
    assert done.status_code == 202
    assert (tmp_path / f'{uid}.svs').read_bytes() == b'abcdef'
    assert not (tmp_path / f'{uid}.svs.partial').exists()
