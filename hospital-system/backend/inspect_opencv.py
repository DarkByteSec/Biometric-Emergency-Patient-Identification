"""Optional dependency inspection only; does not download or create sample images."""
try:
    import cv2
    print(f"opencv={cv2.__version__} lbph={hasattr(cv2, 'face') and hasattr(cv2.face, 'LBPHFaceRecognizer_create')}")
except Exception as exc:
    print(f"opencv=not-installed reason={type(exc).__name__}")
