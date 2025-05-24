from flask import Blueprint
from ..utils import render_template

bp = Blueprint('results', __name__, url_prefix='/results')

@bp.get('/')
def index():
    return render_template('results/index.html')