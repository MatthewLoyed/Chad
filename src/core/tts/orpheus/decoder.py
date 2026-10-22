"""SNAC token decoder for Orpheus TTS audio frames."""

from __future__ import annotations

import threading
from typing import List, Optional

import numpy as np
import torch

_lock = threading.Lock()
_model = None
_device: Optional[str] = None


def _ensure_model():
    global _model, _device
    if _model is not None:
        return _model, _device
    with _lock:
        if _model is not None:
            return _model, _device
        from snac import SNAC

        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
        model = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(device)
        _model = model
        _device = device
        return _model, _device


def turn_token_into_id(token_string: str, index: int) -> Optional[int]:
    token_string = token_string.strip()
    prefix = "<custom_token_"
    start = token_string.rfind(prefix)
    if start == -1:
        return None
    last_token = token_string[start:]
    if not (last_token.startswith(prefix) and last_token.endswith(">")):
        return None
    try:
        number_str = last_token[len(prefix) : -1]
        return int(number_str) - 10 - ((index % 7) * 4096)
    except ValueError:
        return None


def convert_frames_to_pcm(multiframe: List[int]) -> Optional[bytes]:
    if len(multiframe) < 7:
        return None

    model, device = _ensure_model()
    num_frames = len(multiframe) // 7
    frame = multiframe[: num_frames * 7]

    codes_0 = torch.tensor([], device=device, dtype=torch.int32)
    codes_1 = torch.tensor([], device=device, dtype=torch.int32)
    codes_2 = torch.tensor([], device=device, dtype=torch.int32)

    for j in range(num_frames):
        i = 7 * j
        t0 = torch.tensor([frame[i]], device=device, dtype=torch.int32)
        t1a = torch.tensor([frame[i + 1]], device=device, dtype=torch.int32)
        t1b = torch.tensor([frame[i + 4]], device=device, dtype=torch.int32)
        t2a = torch.tensor([frame[i + 2]], device=device, dtype=torch.int32)
        t2b = torch.tensor([frame[i + 3]], device=device, dtype=torch.int32)
        t2c = torch.tensor([frame[i + 5]], device=device, dtype=torch.int32)
        t2d = torch.tensor([frame[i + 6]], device=device, dtype=torch.int32)

        codes_0 = t0 if codes_0.shape[0] == 0 else torch.cat([codes_0, t0])
        codes_1 = torch.cat([t1a, t1b]) if codes_1.shape[0] == 0 else torch.cat([codes_1, t1a, t1b])
        codes_2 = (
            torch.cat([t2a, t2b, t2c, t2d])
            if codes_2.shape[0] == 0
            else torch.cat([codes_2, t2a, t2b, t2c, t2d])
        )

    codes = [codes_0.unsqueeze(0), codes_1.unsqueeze(0), codes_2.unsqueeze(0)]
    for code in codes:
        if torch.any(code < 0) or torch.any(code > 4096):
            return None

    with torch.inference_mode():
        audio_hat = model.decode(codes)
    audio_slice = audio_hat[:, :, 2048:4096].detach().cpu().numpy()
    return (audio_slice * 32767).astype(np.int16).tobytes()
