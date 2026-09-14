import os
import secrets

# HMAC secret for signing our own auth tokens. Set SECRET_KEY in the
# environment so tokens survive restarts and multiple workers.
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

# Everything persistent lives here: the SQLite file and uploaded letters.
# On Railway this should be a mounted volume (e.g. DATA_DIR=/data).
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
LETTERS_DIR = os.path.join(DATA_DIR, "letters")

ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()
]
