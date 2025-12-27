from flask import Blueprint
from .country import bp as c_bp
from .year import bp as y_bp

bp = Blueprint('api', __name__, url_prefix='/api')
bp.register_blueprint(c_bp)
bp.register_blueprint(y_bp)