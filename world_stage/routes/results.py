import datetime
import urllib.parse
import unicodedata
from flask import Blueprint, redirect, render_template, request, url_for
from collections import defaultdict

from ..db import get_db
from ..utils import get_show_id, SuspensefulVoteSequencer, dt_now, get_user_role_from_session, LCG, get_votes_for_song

bp = Blueprint('results', __name__, url_prefix='/results')

@bp.get('/')
def results_index():
    return render_template('results/index.html')