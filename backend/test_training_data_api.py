# Quick smoke tests for Smart Fitness training data feature
import os, sys, json
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auth
from main import app
import main_v2_extra  # noqa: F401 - mounts routes


def test_training_data_endpoint_for_existing_user_has_summary_shape():
    token = auth.generate_token(31, "hjl")
    client = TestClient(app)
    r = client.get("/api/v2/training/data?period=year", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["period"] == "year"
    assert "summary" in data
    assert "sessions" in data
    assert "by_type" in data
    assert isinstance(data["sessions"], list)
    assert data["summary"]["total_reps"] >= 0


def test_training_rep_images_endpoint_shape():
    token = auth.generate_token(31, "hjl")
    client = TestClient(app)
    listing = client.get("/api/v2/training/data?period=year", headers={"Authorization": f"Bearer {token}"}).json()
    reps = [rep for sess in listing.get("sessions", []) for rep in sess.get("reps", [])]
    assert reps, "expected existing rep rows for demo user 31"
    rep_id = reps[0]["id"]
    r = client.get(f"/api/v2/training/rep/{rep_id}/images", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["rep"]["id"] == rep_id
    assert "keyframes" in data
    assert "frames" in data
