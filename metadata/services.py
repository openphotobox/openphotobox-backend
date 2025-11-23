import threading

import numpy as np

_clip_lock = threading.Lock()
_clip_model = None
_clip_preprocess = None
_clip_tokenizer = None
_clip_device = "cpu"


def _l2_normalize(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    return (vec / norm).astype(np.float32) if norm > 0.0 else vec.astype(np.float32)


def get_clip():
    """Load and cache an OpenCLIP ViT-B/32 model, preprocess, and tokenizer for CPU."""
    global _clip_model, _clip_preprocess, _clip_tokenizer
    if _clip_model is not None:
        return _clip_model, _clip_preprocess, _clip_tokenizer

    with _clip_lock:
        if _clip_model is None:
            import open_clip

            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="openai", device=_clip_device
            )
            tokenizer = open_clip.get_tokenizer("ViT-B-32")
            model.eval()

            _clip_model = model
            _clip_preprocess = preprocess
            _clip_tokenizer = tokenizer

    return _clip_model, _clip_preprocess, _clip_tokenizer


def embed_image_bytes(image_bytes: bytes) -> np.ndarray:
    """Compute an L2-normalized CLIP image embedding (512-d float32) from raw bytes."""
    import io

    import torch
    from PIL import Image

    model, preprocess, _ = get_clip()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    image_tensor = preprocess(image).unsqueeze(0)

    with torch.no_grad():
        image_features = model.encode_image(image_tensor)
        image_features = image_features.cpu().numpy().astype(np.float32).squeeze(0)

    return _l2_normalize(image_features)


def embed_text(query: str) -> np.ndarray:
    """Compute an L2-normalized CLIP text embedding (512-d float32) for the given query."""
    import torch

    model, _, tokenizer = get_clip()
    tokens = tokenizer([query])
    with torch.no_grad():
        text_features = model.encode_text(tokens)
        text_features = text_features.cpu().numpy().astype(np.float32).squeeze(0)
    return _l2_normalize(text_features)
