def test_remove_drops_a_camera_without_opening_any(monkeypatch, capsys):
    import sys

    import vision.cams as C
    from vision.tests.synth import target_cams

    saved = {}
    monkeypatch.setattr(C, "load_cameras", lambda: target_cams())
    monkeypatch.setattr(C, "save_cameras", lambda cams: saved.update(cams=cams))
    monkeypatch.setattr(C.cv2, "VideoCapture", lambda *a: (_ for _ in ()).throw(AssertionError("opened a camera")))
    monkeypatch.setattr(sys, "argv", ["cams", "--remove", "A"])
    assert C.main() == 0 and sorted(saved["cams"]) == ["B"]
    assert "removed camera A" in capsys.readouterr().out
