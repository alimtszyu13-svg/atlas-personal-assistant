"""
file_search для облака: только «векторы смысла» — ТОЙ ЖЕ моделью и тем же способом, что на компьютере,
иначе память из Supabase (её векторы посчитаны компьютером) не совпадёт с вопросами.
Поиск файлов на диске и их открытие — недоступны из облака.
"""
import os
import threading

import numpy as np

from cloud.stubs import UNAVAILABLE

EMB_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"     # как в file_search.py компьютера
MODEL_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "atlas_models")
_embedder = None
_emb_lock = threading.Lock()
_ORD = {"первый": 1, "второй": 2, "третий": 3, "first": 1, "second": 2, "third": 3}


EMBED_ON = (os.getenv("ATLAS_EMBED") or "local").strip().lower() not in ("off", "0", "false", "no")
EMB_DIM = 384


def _embed(texts: list) -> np.ndarray:
    """Лёгкий режим (ATLAS_EMBED=off, для серверов с 512 МБ): пустые векторы — память работает по именам
    и «обо мне», без поиска похожих разговоров по смыслу. Векторы новых записей досчитает компьютер."""
    if not EMBED_ON:
        return np.zeros((len(texts), EMB_DIM), dtype=np.float32)
    global _embedder
    with _emb_lock:
        if _embedder is None:
            from fastembed import TextEmbedding
            print("[облако] загружаю модель векторов смысла (та же, что на компьютере)...")
            _embedder = TextEmbedding(EMB_MODEL, cache_dir=MODEL_CACHE)
        vecs = np.array(list(_embedder.embed(texts, batch_size=32)), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.maximum(norms, 1e-9)


def search_file_content(query: str = "", max_results: int = 5) -> str:
    return UNAVAILABLE


def open_found_file(query: str = "") -> str:
    return UNAVAILABLE


def open_search_result(n=1) -> str:
    return UNAVAILABLE


def show_search_result_in_folder(n=1) -> str:
    return UNAVAILABLE


for _f in (search_file_content, open_found_file, open_search_result, show_search_result_in_folder):
    _f.__doc__ = "Недоступно из облака."                 # мозг уберёт их из того, что видит модель


def build_index_background() -> None:
    pass


def index_status() -> str:
    return "Индекс файлов есть только на компьютере."
