from dataclasses import dataclass
import unicodedata
from flask import Blueprint, redirect, request, url_for
from typing import Optional

from ..db import get_db
from ..utils import get_user_id_from_session, format_seconds, parse_seconds, render_template

bp = Blueprint('admin', __name__, url_prefix='/admin')