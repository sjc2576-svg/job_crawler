import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (Vercel의 @vercel/python 런타임이 이 WSGI app을 찾아 실행한다)
