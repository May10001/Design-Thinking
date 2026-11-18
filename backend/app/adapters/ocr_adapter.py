"""Local PaddleOCR adapter. Model files must be prepared locally ahead of startup."""
import os
import tempfile
from pathlib import Path


class OCRAdapter:
    def __init__(self, model_dir):
        root = Path(model_dir)
        det, rec = root / 'det', root / 'rec'
        if not det.is_dir() or not rec.is_dir():
            raise RuntimeError('OCR_MODEL_DIR must contain local det/ and rec/ model directories')
        os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
        from paddleocr import PaddleOCR
        self.pipeline = PaddleOCR(
            text_detection_model_name='PP-OCRv5_mobile_det', text_detection_model_dir=str(det),
            text_recognition_model_name='PP-OCRv5_mobile_rec', text_recognition_model_dir=str(rec),
            use_doc_orientation_classify=False, use_doc_unwarping=False,
            use_textline_orientation=False, device='cpu',
        )

    def recognize(self, image_bytes):
        with tempfile.TemporaryDirectory(prefix='deadline-ocr-') as directory:
            path = Path(directory) / 'input.png'
            path.write_bytes(image_bytes)
            results = list(self.pipeline.predict(str(path)))
        texts, scores = [], []
        for result in results:
            payload = result.json
            if callable(payload):
                payload = payload()
            payload = payload.get('res', payload)
            texts.extend(payload.get('rec_texts', []))
            scores.extend(payload.get('rec_scores', []))
        warnings = []
        if scores and sum(scores) / len(scores) < 0.7:
            warnings.append({'code':'OCR_LOW_QUALITY','message':'部分文字识别质量较低，请核对'})
        return '\n'.join(str(t).strip() for t in texts if str(t).strip()), warnings
