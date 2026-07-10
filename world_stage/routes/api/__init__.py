from flask import Blueprint

from .country import bp as c_bp
from .discovery import bp as d_bp
from .song import bp as s_bp
from .voting import bp as v_bp
from .year import bp as y_bp

bp = Blueprint("api", __name__, url_prefix="/api")
bp.register_blueprint(c_bp)
bp.register_blueprint(s_bp)
bp.register_blueprint(y_bp)
bp.register_blueprint(d_bp)
bp.register_blueprint(v_bp)
