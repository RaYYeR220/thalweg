import cv2


def test_opencv5():
    assert cv2.__version__.startswith("5.")
    for name in ("ALIKED_create", "LightGlueMatcher_create", "DISOpticalFlow_create", "findHomography", "solvePnP"):
        assert hasattr(cv2, name), name
